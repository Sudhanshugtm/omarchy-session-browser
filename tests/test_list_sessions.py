import importlib.machinery
import importlib.util
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader(
    "session_browser_list_sessions", str(PLUGIN_ROOT / "list-sessions"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
SESSIONS = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(SESSIONS)


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        SESSIONS.HOME = str(self.home)

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, relative, data):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def write_text(self, relative, text):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_jsonl(self, relative, records):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return path

    def test_codex_keeps_user_threads_and_excludes_subagents(self):
        parent_id = "11111111-1111-4111-8111-111111111111"
        child_id = "22222222-2222-4222-8222-222222222222"
        root = Path(".codex/sessions/2026/08/27")
        self.write_jsonl(
            root / f"rollout-2026-08-27T10-00-00-{parent_id}.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": parent_id,
                        "cwd": str(self.home / "project"),
                        "thread_source": "user",
                        "source": "cli",
                        "parent_thread_id": None,
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "Main task"},
                },
            ],
        )
        self.write_jsonl(
            root / f"rollout-2026-08-27T10-01-00-{child_id}.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": child_id,
                        "cwd": str(self.home / "project"),
                        "thread_source": "subagent",
                        "source": {"subagent": {"thread_spawn": {}}},
                        "parent_thread_id": parent_id,
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "Delegated task"},
                },
            ],
        )

        rows = list(SESSIONS.scan_codex())
        self.assertEqual([row["id"] for row in rows], [parent_id])
        self.assertEqual(rows[0]["title"], "Main task")

    def test_claude_only_scans_top_level_conversations(self):
        session_id = "33333333-3333-4333-8333-333333333333"
        project = Path(".claude/projects/-tmp-project")
        self.write_jsonl(
            project / f"{session_id}.jsonl",
            [{
                "type": "user",
                "isSidechain": False,
                "cwd": str(self.home / "project"),
                "message": {"content": "Main Claude task"},
            }],
        )
        self.write_jsonl(
            project / session_id / "subagents/agent-child.jsonl",
            [{
                "type": "user",
                "isSidechain": True,
                "agentId": "child",
                "message": {"content": "Nested child"},
            }],
        )
        self.write_jsonl(
            project / "agent-defensive.jsonl",
            [{
                "type": "user",
                "isSidechain": True,
                "agentId": "defensive",
                "message": {"content": "Flat child"},
            }],
        )

        rows = list(SESSIONS.scan_claude())
        self.assertEqual([row["id"] for row in rows], [session_id])

    def test_opencode_only_selects_rows_without_a_parent(self):
        db = self.home / ".local/share/opencode/opencode.db"
        db.parent.mkdir(parents=True)
        con = sqlite3.connect(db)
        con.execute(
            "create table session ("
            "id text primary key, project_id text, workspace_id text, parent_id text, "
            "directory text, title text, time_updated integer, time_created integer, "
            "model text, agent text, cost real, tokens_input integer, "
            "tokens_output integer, tokens_reasoning integer, slug text)"
        )
        row = (
            "ses_parent", "project", None, None, str(self.home), "Parent", 2000,
            1000, '{"id":"model"}', "build", 0.0, 10, 5, 0, "parent",
        )
        con.execute("insert into session values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        child = list(row)
        child[0], child[3], child[5], child[6] = "ses_child", "ses_parent", "Child", 3000
        con.execute("insert into session values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", child)
        con.commit()
        con.close()

        byte_budget = SESSIONS.ScanBudget(seconds=1, max_bytes=1)
        self.assertEqual(list(SESSIONS.scan_opencode(byte_budget)), [])
        self.assertIn("byte limit", byte_budget.reasons)

        rows = list(SESSIONS.scan_opencode())
        self.assertEqual([item["id"] for item in rows], ["ses_parent"])

    def test_copilot_reads_modern_and_legacy_top_level_sessions(self):
        modern_id = "44444444-4444-4444-8444-444444444444"
        child_id = "55555555-5555-4555-8555-555555555555"
        modern = Path(".copilot/session-state")
        self.write_jsonl(
            modern / modern_id / "events.jsonl",
            [
                {
                    "type": "session.start",
                    "data": {
                        "sessionId": modern_id,
                        "context": {"cwd": str(self.home / "modern-project")},
                    },
                    "parentId": None,
                },
                {"type": "subagent.started", "data": {"agentId": "worker"}},
                {"type": "user.message", "data": {"content": "Modern task"}},
            ],
        )
        self.write_jsonl(
            modern / child_id / "events.jsonl",
            [{
                "type": "session.start",
                "data": {
                    "sessionId": child_id,
                    "kind": "subagent",
                    "parentSessionId": modern_id,
                    "context": {"cwd": str(self.home / "modern-project")},
                },
            }],
        )
        self.write_json(
            ".copilot/history-session-state/legacy.json",
            {"summary": "Legacy task", "cwd": str(self.home / "legacy-project")},
        )
        self.write_json(
            ".copilot/history-session-state/legacy-child.json",
            {"summary": "Legacy child", "kind": "subagent"},
        )

        rows = list(SESSIONS.scan_copilot())
        self.assertEqual({row["id"] for row in rows}, {modern_id, "legacy"})
        self.assertEqual(next(row for row in rows if row["id"] == modern_id)["title"],
                         "Modern task")

    def test_pi_keeps_user_forks_but_skips_explicit_subagents(self):
        root = Path(".pi/agent/sessions/--tmp-project--")
        parent = self.write_jsonl(
            root / "parent.jsonl",
            [
                {"type": "session", "id": "parent", "cwd": str(self.home)},
                {"type": "message", "message": {"role": "user", "content": "Parent"}},
            ],
        )
        fork = self.write_jsonl(
            root / "fork.jsonl",
            [
                {
                    "type": "session",
                    "id": "fork",
                    "cwd": str(self.home),
                    "parentSession": str(parent),
                },
                {"type": "message", "message": {"role": "user", "content": "Fork"}},
            ],
        )
        self.write_jsonl(
            root / "child.jsonl",
            [{"type": "session", "id": "child", "kind": "subagent"}],
        )

        rows = list(SESSIONS.scan_pi())
        self.assertEqual({Path(row["id"]).name for row in rows},
                         {parent.name, fork.name})

    def test_gemini_reads_json_and_jsonl_without_nested_children(self):
        project_root = self.home / "gemini-project"
        project_root.mkdir()
        project = Path(".gemini/tmp/gemini-project")
        self.write_text(project / ".project_root", str(project_root))
        chats = project / "chats"
        main_id = "66666666-6666-4666-8666-666666666666"
        legacy_id = "77777777-7777-4777-8777-777777777777"
        self.write_jsonl(
            chats / "session-2026-08-27T10-00-main.jsonl",
            [
                {
                    "sessionId": main_id,
                    "projectHash": "hash",
                    "kind": "main",
                    "startTime": "2026-08-27T10:00:00Z",
                },
                {"id": "message-1", "type": "user", "content": "Gemini task"},
            ],
        )
        self.write_jsonl(
            chats / main_id / "agent-child.jsonl",
            [{"sessionId": "child", "projectHash": "hash", "kind": "subagent"}],
        )
        self.write_jsonl(
            chats / "session-defensive-child.jsonl",
            [{"sessionId": "flat-child", "projectHash": "hash", "kind": "subagent"}],
        )
        self.write_json(
            chats / "session-legacy.json",
            {
                "sessionId": legacy_id,
                "projectHash": "hash",
                "kind": "main",
                "messages": [{"role": "user", "content": "Legacy Gemini task"}],
            },
        )

        rows = list(SESSIONS.scan_gemini())
        self.assertEqual({row["id"] for row in rows}, {main_id, legacy_id})
        self.assertTrue(all(row["dir"] == str(project_root) for row in rows))

    def test_grok_keeps_user_forks_and_excludes_subagent_sessions(self):
        parent_id = "99999999-9999-4999-8999-999999999999"
        fork_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        child_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        legacy_child_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        sessions = Path(".grok/sessions/%2Ftmp%2Fproject")

        def summary(sid, title, **extra):
            return {
                "info": {"id": sid, "cwd": str(self.home / "project")},
                "generated_title": title,
                "created_at": "2026-08-27T10:00:00Z",
                "updated_at": "2026-08-27T10:05:00Z",
                "num_chat_messages": 8,
                "current_model_id": "grok-code-fast-1",
                **extra,
            }

        self.write_json(sessions / parent_id / "summary.json",
                        summary(parent_id, "Main Grok task"))
        self.write_json(
            sessions / fork_id / "summary.json",
            summary(fork_id, "User fork", session_kind="fork",
                    parent_session_id=parent_id),
        )
        self.write_json(
            sessions / child_id / "summary.json",
            summary(child_id, "Delegated task", session_kind="subagent"),
        )
        self.write_json(
            sessions / legacy_child_id / "summary.json",
            summary(legacy_child_id, "Legacy delegated task"),
        )
        self.write_json(
            sessions / parent_id / "subagents" / legacy_child_id / "meta.json",
            {"child_session_id": legacy_child_id},
        )

        with mock.patch.dict(
            os.environ, {"GROK_HOME": str(self.home / ".grok")}
        ):
            rows = list(SESSIONS.scan_grok())

        self.assertEqual({row["id"] for row in rows}, {parent_id, fork_id})
        self.assertEqual(next(row for row in rows if row["id"] == parent_id)["title"],
                         "Main Grok task")
        self.assertIn("grok-code-fast-1", rows[0]["meta"])

    def test_agy_lists_conversations_and_excludes_nested_ones(self):
        workspace = self.home / "git" / "map-fix"
        workspace.mkdir(parents=True)
        db = self.home / ".gemini/antigravity-cli/conversation_summaries.db"
        db.parent.mkdir(parents=True)
        con = sqlite3.connect(db)
        con.execute(
            "create table conversation_summaries ("
            "conversation_id text primary key, title text, preview text, "
            "step_count integer, last_modified_time datetime, "
            "workspace_uris text, status text, source text, project_id text, "
            "agent_name text, parent_conversation_id text, nesting_depth integer, "
            "killed numeric, last_user_input_time datetime, app_data_dir text)"
        )
        parent_id = "27a2cb3f-5a80-4b4a-b97c-740a72cc74c9"
        preview_id = "7c1a2e2f-1b6d-48e3-82a3-368fd1c42573"
        child_id = "63063499-ece1-4968-9176-774eb1730786"
        rows = [
            (parent_id, "Titled conversation", "Preview text", 42,
             "2026-08-27 10:00:00.868523566+00:00",
             json.dumps(["file://" + str(workspace)]), "", "", "proj",
             "", "", 0, 0, "2026-08-27 09:00:00+00:00", "antigravity-cli"),
            (preview_id, "", "Falls back to preview", 3,
             "2026-08-26 10:00:00+00:00",
             json.dumps(["file:///nonexistent/path"]), "", "", "proj",
             "", "", 0, 0, "2026-08-26 09:00:00+00:00", "antigravity-cli"),
            (child_id, "Delegated task", "Delegated", 5,
             "2026-08-28 10:00:00+00:00",
             json.dumps(["file://" + str(workspace)]), "", "", "proj",
             "", parent_id, 1, 0, "2026-08-28 09:00:00+00:00", "antigravity-cli"),
        ]
        con.executemany(
            "insert into conversation_summaries values "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()

        found = list(SESSIONS.scan_agy())
        self.assertEqual([row["id"] for row in found], [parent_id, preview_id])
        by_id = {row["id"]: row for row in found}
        self.assertEqual(by_id[parent_id]["title"], "Titled conversation")
        self.assertEqual(by_id[parent_id]["dir"], str(workspace))
        self.assertIn("42 steps", by_id[parent_id]["meta"])
        self.assertGreater(by_id[parent_id]["mtime"], 0)
        self.assertEqual(by_id[preview_id]["title"], "Falls back to preview")
        self.assertEqual(by_id[preview_id]["dir"], str(self.home))

    def test_resume_targets_the_selected_sessions(self):
        old_find = SESSIONS.find
        old_open_terminal = SESSIONS.open_terminal
        commands = []
        SESSIONS.find = lambda _agent, _sid: {"dir": str(self.home)}
        SESSIONS.open_terminal = commands.append
        try:
            SESSIONS.resume("opencode", "ses_selected")
            SESSIONS.resume("gemini", "88888888-8888-4888-8888-888888888888")
            SESSIONS.resume("grok", "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
            SESSIONS.resume("agy", "27a2cb3f-5a80-4b4a-b97c-740a72cc74c9")
        finally:
            SESSIONS.find = old_find
            SESSIONS.open_terminal = old_open_terminal

        self.assertIn("opencode --session 'ses_selected'", commands[0])
        self.assertIn(
            "gemini --resume '88888888-8888-4888-8888-888888888888'",
            commands[1],
        )
        self.assertIn(
            "grok --resume 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'",
            commands[2],
        )
        self.assertIn(
            "agy --conversation '27a2cb3f-5a80-4b4a-b97c-740a72cc74c9'",
            commands[3],
        )

    def test_special_files_and_leaf_symlinks_are_never_opened(self):
        project = Path(".claude/projects/-tmp-project")
        regular_id = "10101010-1010-4010-8010-101010101010"
        regular = self.write_jsonl(
            project / f"{regular_id}.jsonl",
            [{
                "type": "user",
                "isSidechain": False,
                "cwd": str(self.home),
                "message": {"content": "Regular session"},
            }],
        )
        fifo = self.home / project / "20202020-2020-4020-8020-202020202020.jsonl"
        os.mkfifo(fifo)
        symlink = self.home / project / "30303030-3030-4030-8030-303030303030.jsonl"
        symlink.symlink_to(regular)

        budget = SESSIONS.ScanBudget(seconds=1)
        started = time.monotonic()
        rows = list(SESSIONS.scan_claude(budget))

        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual([row["id"] for row in rows], [regular_id])
        self.assertIn("non-regular path", budget.reasons)

    def test_candidate_and_open_file_limits_return_partial_results(self):
        project = Path(".claude/projects/-tmp-project")
        for index in range(5):
            sid = f"40404040-4040-4040-8040-{index:012d}"
            self.write_jsonl(
                project / f"{sid}.jsonl",
                [{"type": "user", "message": {"content": f"Session {index}"}}],
            )

        with mock.patch.object(SESSIONS, "CANDIDATE_CAP", 3):
            candidate_budget = SESSIONS.ScanBudget(seconds=1)
            rows = list(SESSIONS.scan_claude(candidate_budget))
        self.assertEqual(len(rows), 3)
        self.assertIn("candidate limit", candidate_budget.reasons)

        open_budget = SESSIONS.ScanBudget(seconds=1, max_open_files=1)
        rows = list(SESSIONS.scan_claude(open_budget))
        self.assertEqual(len(rows), 1)
        self.assertIn("open-file limit", open_budget.reasons)

    def test_byte_and_deadline_budgets_stop_before_parsing(self):
        path = self.write_text(
            ".codex/sessions/2026/08/27/rollout-budget.jsonl",
            json.dumps({"type": "event_msg", "payload": {"message": "x" * 512}})
            + "\n",
        )
        byte_budget = SESSIONS.ScanBudget(seconds=1, max_bytes=64)
        self.assertEqual(SESSIONS.jsonl_head(path, 10, byte_budget), [])
        self.assertLessEqual(byte_budget.bytes, 64)
        self.assertIn("byte limit", byte_budget.reasons)

        deadline_budget = SESSIONS.ScanBudget(seconds=0)
        self.assertEqual(list(SESSIONS.scan_codex(deadline_budget)), [])
        self.assertIn("deadline", deadline_budget.reasons)

    def test_process_deadline_interrupts_a_stalled_provider(self):
        def stalled(_budget):
            time.sleep(1)
            yield {
                "id": "never-reached",
                "mtime": 0,
                "title": "stalled",
                "dir": str(self.home),
            }

        budget = SESSIONS.ScanBudget(seconds=0.02)
        started = time.monotonic()
        with mock.patch.object(SESSIONS, "SCANNERS", {"codex": stalled}):
            result = SESSIONS.collect(budget)

        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(result["sessions"], [])
        self.assertTrue(result["limited"])
        self.assertIn("deadline", result["limitReasons"])

    def test_oversized_json_and_fifo_database_are_skipped(self):
        summary = self.home / ".grok/sessions/project/session/summary.json"
        summary.parent.mkdir(parents=True)
        summary.touch()
        os.truncate(summary, SESSIONS.JSON_FILE_MAX + 1)
        grok_budget = SESSIONS.ScanBudget(seconds=1)
        self.assertEqual(list(SESSIONS.scan_grok(grok_budget)), [])
        self.assertIn("per-file byte limit", grok_budget.reasons)

        database = self.home / ".local/share/opencode/opencode.db"
        database.parent.mkdir(parents=True)
        os.mkfifo(database)
        db_budget = SESSIONS.ScanBudget(seconds=1)
        started = time.monotonic()
        self.assertEqual(list(SESSIONS.scan_opencode(db_budget)), [])
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertIn("non-regular path", db_budget.reasons)

        database.unlink()
        con = sqlite3.connect(database)
        con.execute(
            "create table session (id text, title text, directory text, "
            "time_updated integer, time_created integer, model text, agent text, "
            "cost real, tokens_input integer, tokens_output integer, "
            "tokens_reasoning integer, slug text, parent_id text)"
        )
        con.close()
        os.mkfifo(str(database) + "-wal")
        auxiliary_budget = SESSIONS.ScanBudget(seconds=1)
        self.assertEqual(list(SESSIONS.scan_opencode(auxiliary_budget)), [])
        self.assertIn("non-regular path", auxiliary_budget.reasons)

    def test_opencode_peek_streams_at_most_400_rows(self):
        database = self.home / ".local/share/opencode/opencode.db"
        database.parent.mkdir(parents=True)
        con = sqlite3.connect(database)
        con.execute("create table message (id text primary key, data text)")
        con.execute(
            "create table part (session_id text, message_id text, "
            "time_created integer, data text)"
        )
        for index in range(450):
            message_id = f"msg-{index}"
            con.execute("insert into message values (?, ?)",
                        (message_id, json.dumps({"role": "assistant"})))
            con.execute("insert into part values (?, ?, ?, ?)",
                        ("ses_bounded", message_id, index,
                         json.dumps({"type": "text", "text": "hello"})))
        con.commit()
        con.close()

        rendered = []
        old_find = SESSIONS.find
        old_render = SESSIONS.render_block
        SESSIONS.find = lambda _agent, _sid: {
            "agentName": "opencode", "dirShort": "~", "meta": ""
        }
        SESSIONS.render_block = lambda role, text: rendered.append((role, text))
        SESSIONS._blocks_left[0] = 400
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                SESSIONS.peek_render("opencode", "ses_bounded")
        finally:
            SESSIONS.find = old_find
            SESSIONS.render_block = old_render
            SESSIONS._blocks_left[0] = 400

        self.assertEqual(len(rendered), 400)

    def test_peek_render_has_an_independent_hard_deadline(self):
        old_find = SESSIONS.find
        SESSIONS.find = lambda _agent, _sid: None
        started = time.monotonic()
        try:
            with mock.patch.object(SESSIONS, "render_transcript",
                                      side_effect=lambda *_args: time.sleep(4)), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                # peek_render uses an explicit three-second budget; patch the
                # constructor to make that ceiling fast in the fixture.
                original_budget = SESSIONS.ScanBudget

                def short_budget(*_args, **kwargs):
                    return original_budget(seconds=0.02,
                                           max_paths=kwargs.get("max_paths", 64),
                                           max_open_files=kwargs.get("max_open_files", 64),
                                           max_bytes=kwargs.get("max_bytes",
                                                                SESSIONS.MAX_SCAN_BYTES))

                with mock.patch.object(SESSIONS, "ScanBudget", side_effect=short_budget):
                    SESSIONS.peek_render("codex", "50505050-5050-4050-8050-505050505050")
        finally:
            SESSIONS.find = old_find

        self.assertLess(time.monotonic() - started, 0.5)
        self.assertIn("safety limit", output.getvalue())

    def test_encoded_output_is_bounded_inside_the_helper(self):
        sessions = []
        for index in range(400):
            sessions.append({
                "agent": "codex",
                "id": f"{index:012d}",
                "title": "t" * SESSIONS.TITLE_MAX,
                "dir": "/" + "d" * SESSIONS.PATH_TEXT_MAX,
                "dirShort": "/" + "d" * SESSIONS.PATH_TEXT_MAX,
                "mtime": index,
                "meta": "m" * SESSIONS.META_TEXT_MAX,
            })
        payload = {
            "sessions": sessions,
            "agents": SESSIONS.present_agents(sessions),
            "limited": False,
            "limitReasons": [],
            "scanStats": {"paths": 0, "files": 0, "bytes": 0},
        }

        original_dumps = json.dumps
        with mock.patch.object(SESSIONS.json, "dumps", wraps=original_dumps) as dumps:
            encoded = SESSIONS.encode_payload(payload)
        parsed = json.loads(encoded)
        self.assertLessEqual(len(encoded.encode("utf-8")), SESSIONS.OUTPUT_MAX)
        self.assertTrue(parsed["limited"])
        self.assertIn("output limit", parsed["limitReasons"])
        self.assertLess(len(parsed["sessions"]), 400)
        self.assertLessEqual(dumps.call_count, 12)

    def test_malformed_nesting_and_terminal_controls_fail_closed(self):
        nested = self.home / ".grok/sessions/project/session/summary.json"
        nested.parent.mkdir(parents=True)
        nested.write_text('{"value":' * 1200 + "null" + "}" * 1200,
                          encoding="utf-8")
        budget = SESSIONS.ScanBudget(seconds=1)
        self.assertIsNone(SESSIONS.read_json_file(nested, budget))
        self.assertIn("JSON depth limit", budget.reasons)
        braces_in_text = json.dumps({"value": "{" * 200 + '\\"' + "}" * 200})
        self.assertIsNotNone(SESSIONS.parse_json_dict(braces_in_text))

        normalized = SESSIONS.normalize_session({
            "id": "11111111-1111-4111-8111-111111111111",
            "title": "Safe\x1b]52;c;clipboard\x07 title",
            "dir": str(self.home) + "/work\x9b31m",
            "mtime": 1,
            "meta": "model\x1b[31m red",
        }, "codex", SESSIONS.ScanBudget(seconds=1))
        self.assertNotRegex(normalized["title"], r"[\x00-\x1f\x7f-\x9f]")
        self.assertNotRegex(normalized["dirShort"], r"[\x00-\x1f\x7f-\x9f]")
        self.assertNotRegex(normalized["meta"], r"[\x00-\x1f\x7f-\x9f]")

        SESSIONS._blocks_left[0] = 1
        try:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                SESSIONS.render_block("assistant\x1b]0;spoof\x07",
                                      "hello\x1b]52;c;payload\x07")
        finally:
            SESSIONS._blocks_left[0] = 400
        rendered = output.getvalue()
        self.assertNotIn("\x1b]0;", rendered)
        self.assertNotIn("\x1b]52;", rendered)


if __name__ == "__main__":
    unittest.main()
