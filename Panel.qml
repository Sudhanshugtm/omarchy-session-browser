import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "sid.sessions"
  ipcTarget: "sid.sessions"

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  property var sessions: []
  property var agents: []
  property string filter: "all"
  property int selectedIndex: 0
  property bool scanning: false
  property bool scanLimited: false
  property string scanNotice: ""
  property bool confirmingDelete: false
  property int copiedIndex: -1
  // Rows slide under a stationary cursor while the panel animates open;
  // only treat hover as selection when the pointer itself has moved.
  property real lastMouseX: -1
  property real lastMouseY: -1
  // Hover-selection stays disarmed briefly after opening: while the panel
  // animates in, rows sweep under the stationary cursor and would steal
  // the selection from the keyboard default (top row).
  property bool hoverArmed: false

  onSelectedIndexChanged: {
    root.confirmingDelete = false
    if (typeof sessionList !== "undefined" && sessionList.count > 0)
      sessionList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
  }
  onFilterChanged: root.confirmingDelete = false

  readonly property string helper: Qt.resolvedUrl("list-sessions").toString().replace(/^file:\/\//, "")
  readonly property color fg: root.bar ? root.bar.foreground : Color.foreground
  readonly property color dimmed: Qt.darker(fg, 1.4)
  readonly property color faint: Qt.darker(fg, 1.9)

  readonly property var shown: {
    var list = root.filter === "all"
      ? root.sessions
      : root.sessions.filter(s => s.agent === root.filter)
    return list
  }

  function open() {
    root.controller.show()
    root.hoverArmed = false
    hoverArm.restart()
    refresh()
  }
  function openFromHotkey() { open() }
  function close() { root.controller.hide() }
  function toggle() { root.opened ? close() : open() }
  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function refresh() {
    root.selectedIndex = 0
    if (!listProc.running) {
      root.scanLimited = false
      root.scanNotice = ""
      listProc.timedOut = false
      root.scanning = true
      listProc.running = true
    }
  }

  function cycleFilter(step) {
    var keys = ["all"].concat(root.agents.map(a => a.agent))
    var i = keys.indexOf(root.filter)
    if (i < 0) i = 0
    root.filter = keys[(i + step + keys.length) % keys.length]
    root.selectedIndex = 0
  }

  function resumeSelected() {
    if (root.shown.length > 0) resumeSession(root.shown[root.selectedIndex])
  }

  function resumeSession(s) {
    if (!s) return
    runAction("resume", s)
    root.close()
  }

  function selected() {
    return root.shown.length > 0 ? root.shown[root.selectedIndex] : null
  }

  function runAction(action, s) {
    if (!s || actionProc.running) return
    if (action === "delete" && !s.canDelete) return
    if (action === "peek" && !s.canPeek) return
    actionProc.pendingRefresh = (action === "delete")
    actionProc.command = ["python3", root.helper, action, s.agent, String(s.id)]
    actionProc.running = true
    if (action === "copy") {
      root.copiedIndex = root.selectedIndex
      copiedReset.restart()
    }
    if (action === "peek" || action === "folder") root.close()
  }

  function requestDelete() {
    var s = selected()
    if (!s || !s.canDelete) return
    if (!root.confirmingDelete) {
      root.confirmingDelete = true
      return
    }
    root.confirmingDelete = false
    runAction("delete", s)
  }

  Timer {
    id: hoverArm
    interval: 800
    onTriggered: root.hoverArmed = true
  }

  Timer {
    id: copiedReset
    interval: 1500
    onTriggered: root.copiedIndex = -1
  }

  Timer {
    id: scanDeadline
    interval: 6000
    onTriggered: {
      if (!listProc.running) return
      listProc.timedOut = true
      root.scanning = false
      root.scanLimited = true
      root.scanNotice = "Scan stopped after 6 seconds"
      listProc.running = false
    }
  }

  Timer {
    id: actionDeadline
    interval: 8000
    onTriggered: if (actionProc.running) actionProc.running = false
  }

  function fmtAge(epoch) {
    var sec = Math.max(0, Math.round(Date.now() / 1000 - Number(epoch)))
    if (sec < 60) return "just now"
    if (sec < 5400) return Math.round(sec / 60) + " min ago"
    if (sec < 129600) return Math.round(sec / 3600) + " h ago"
    return Math.round(sec / 86400) + " d ago"
  }

  Process {
    id: actionProc
    property bool pendingRefresh: false
    onRunningChanged: running ? actionDeadline.restart() : actionDeadline.stop()
    onExited: if (pendingRefresh) { pendingRefresh = false; root.refresh() }
  }

  Process {
    id: listProc
    property bool timedOut: false
    command: ["python3", root.helper]
    onRunningChanged: running ? scanDeadline.restart() : scanDeadline.stop()
    onExited: root.scanning = false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (listProc.timedOut) return
        root.scanning = false
        if (text.length > 1048576) {
          root.sessions = []
          root.agents = []
          root.scanLimited = true
          root.scanNotice = "Collector output exceeded 1 MiB"
          return
        }
        try {
          var parsed = JSON.parse(text)
          root.sessions = parsed.sessions || []
          root.agents = parsed.agents || []
          root.scanLimited = parsed.limited === true
          root.scanNotice = root.scanLimited ? "Partial results · scan limit reached" : ""
        } catch (e) {
          root.sessions = []
          root.agents = []
          root.scanLimited = true
          root.scanNotice = "Could not read session results"
        }
        if (root.selectedIndex >= root.shown.length)
          root.selectedIndex = Math.max(0, root.shown.length - 1)
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: false
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(520))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (dy !== 0 && root.shown.length > 0)
          root.selectedIndex = Math.max(0, Math.min(root.shown.length - 1, root.selectedIndex + dy))
        if (dx !== 0) root.cycleFilter(dx)
      }
      onReturnRequested: root.resumeSelected()
      onActivateRequested: root.resumeSelected()
      onTextKey: function(text) {
        if (text === "r") root.refresh()
        else if (text === "p") root.runAction("peek", root.selected())
        else if (text === "o") root.runAction("folder", root.selected())
        else if (text === "y") root.runAction("copy", root.selected())
        else if (text === "d") root.requestDelete()
      }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(6)

        // ---- Hero: title + counts.
        Item {
          width: parent.width
          height: Style.space(52)

          Column {
            anchors.left: parent.left
            anchors.leftMargin: Style.space(16)
            anchors.right: parent.right
            anchors.rightMargin: Style.space(16)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              width: parent.width
              text: "Sessions"
              color: root.fg
              font.family: root.bar ? root.bar.fontFamily : Style.font.family
              font.pixelSize: Style.font.title
              font.bold: true
            }

            Text {
              width: parent.width
              text: root.scanning ? "Scanning agents…"
                : (root.scanNotice !== "" ? root.scanNotice
                  : (root.sessions.length === 0 ? "No sessions found"
                  : (root.agents.length > 4
                    ? root.sessions.length + " sessions · " + root.agents.length + " agents"
                    : root.agents.map(a => a.count + " " + a.name).join(" · "))))
              elide: Text.ElideRight
              color: root.dimmed
              font.family: root.bar ? root.bar.fontFamily : Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }

        // ---- Agent filter pills.
        Flow {
          width: parent.width - Style.space(32)
          x: Style.space(16)
          spacing: Style.space(6)
          visible: root.agents.length > 1

          Repeater {
            model: [{agent: "all", name: "All", count: root.sessions.length}].concat(root.agents)

            Rectangle {
              required property var modelData
              readonly property bool active: root.filter === modelData.agent
              readonly property color brand: String(modelData.color || "") !== ""
                ? modelData.color : Color.accent

              readonly property bool hasLogo: String(modelData.iconFile || "") !== ""
              width: pillRow.implicitWidth + Style.space(20)
              height: pillText.implicitHeight + Style.space(10)
              radius: height / 2
              color: active ? brand
                : (pillArea.containsMouse
                  ? Style.hoverFillFor(root.fg, Color.accent) : "transparent")
              border.width: active ? 0 : 1
              border.color: root.faint

              Row {
                id: pillRow
                anchors.centerIn: parent
                spacing: Style.space(5)

                Image {
                  anchors.verticalCenter: parent.verticalCenter
                  visible: pillRow.parent.hasLogo && !pillRow.parent.active
                  source: pillRow.parent.hasLogo ? Qt.resolvedUrl(modelData.iconFile) : ""
                  width: Style.space(11)
                  height: Style.space(11)
                  sourceSize: Qt.size(Style.space(11) * 2, Style.space(11) * 2)
                  smooth: true
                }

                Text {
                  id: pillText
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.name + "  " + modelData.count
                  textFormat: Text.PlainText
                  color: pillRow.parent.active ? Color.background : root.fg
                  font.family: root.bar ? root.bar.fontFamily : Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: pillRow.parent.active
                }
              }

              MouseArea {
                id: pillArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  root.filter = parent.modelData.agent
                  root.selectedIndex = 0
                }
              }
            }
          }
        }

        PanelSeparator { width: parent.width }

        // ---- Session rows (scrollable).
        ListView {
          id: sessionList
          width: parent.width
          height: Math.min(contentHeight, Style.space(500))
          clip: true
          spacing: Style.space(2)
          boundsBehavior: Flickable.StopAtBounds
          model: root.shown

          delegate: Rectangle {
            id: row
            required property var modelData
            required property int index
            readonly property bool current: index === root.selectedIndex

            width: sessionList.width
            height: rowContent.implicitHeight + Style.space(16)
            radius: Style.cornerRadius
            color: (current || rowArea.containsMouse)
              ? Style.hoverFillFor(root.fg, Color.accent) : "transparent"


            // Agent logo badge in the vendor's brand color.
            Rectangle {
              id: badge
              readonly property color brand: String(row.modelData.color || "") !== ""
                ? row.modelData.color : Color.accent
              anchors.left: parent.left
              anchors.leftMargin: Style.space(14)
              anchors.top: parent.top
              anchors.topMargin: Style.space(8)
              readonly property bool hasLogo: String(row.modelData.iconFile || "") !== ""
              width: Style.space(34)
              height: Style.space(30)
              radius: Style.cornerRadius
              color: row.current ? Qt.alpha(brand, 0.38) : Qt.alpha(brand, 0.14)

              Image {
                anchors.centerIn: parent
                visible: badge.hasLogo
                source: badge.hasLogo ? Qt.resolvedUrl(row.modelData.iconFile) : ""
                width: Style.space(17)
                height: Style.space(17)
                sourceSize: Qt.size(Style.space(17) * 2, Style.space(17) * 2)
                smooth: true
              }

              Text {
                anchors.centerIn: parent
                visible: !badge.hasLogo
                text: row.modelData.icon || row.modelData.badge
                color: badge.brand
                font.family: String(row.modelData.iconFont || "") !== ""
                  ? row.modelData.iconFont
                  : (root.bar ? root.bar.fontFamily : Style.font.family)
                font.pixelSize: Style.font.body
              }
            }

            Column {
              id: rowContent
              // Above the row-wide MouseArea so the control pills get clicks.
              z: 1
              anchors.left: badge.right
              anchors.leftMargin: Style.space(12)
              anchors.right: rowAge.left
              anchors.rightMargin: Style.space(12)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              Text {
                id: rowTitle
                width: parent.width
                text: row.modelData.title
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: root.fg
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: Style.font.body
                font.bold: row.current
              }

              Text {
                id: rowSub
                width: parent.width
                text: row.modelData.agentName + " · " + row.modelData.dirShort
                textFormat: Text.PlainText
                elide: Text.ElideMiddle
                color: root.dimmed
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: Style.font.caption
              }

              // Metadata line, shown on the selected row.
              Text {
                width: parent.width
                visible: row.current && String(row.modelData.meta || "") !== ""
                text: String(row.modelData.meta || "")
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: root.dimmed
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: Style.font.caption
              }

              // Sub-controls, shown on the selected row.
              Row {
                visible: row.current
                spacing: Style.space(6)
                topPadding: Style.space(4)

                Repeater {
                  model: {
                    if (!row.current) return []
                    var acts = [{key: "resume", label: "↵ Resume", danger: false}]
                    if (row.modelData.canPeek)
                      acts.push({key: "peek", label: "p Peek", danger: false})
                    acts.push({key: "folder", label: "o Folder", danger: false})
                    acts.push({key: "copy",
                      label: root.copiedIndex === row.index ? "✓ Copied" : "y Copy ID",
                      danger: false})
                    if (row.modelData.canDelete)
                      acts.push({key: "delete",
                        label: root.confirmingDelete ? "d Sure?" : "d Delete",
                        danger: true})
                    return acts
                  }

                  Rectangle {
                    required property var modelData
                    width: ctlText.implicitWidth + Style.space(16)
                    height: ctlText.implicitHeight + Style.space(8)
                    radius: height / 2
                    color: ctlArea.containsMouse
                      ? (modelData.danger && root.confirmingDelete
                        ? Color.urgent : Color.accent)
                      : "transparent"
                    border.width: 1
                    border.color: modelData.danger && root.confirmingDelete
                      ? Color.urgent : root.faint

                    Text {
                      id: ctlText
                      anchors.centerIn: parent
                      text: parent.modelData.label
                      textFormat: Text.PlainText
                      color: ctlArea.containsMouse ? Color.background
                        : (parent.modelData.danger && root.confirmingDelete
                          ? Color.urgent : root.dimmed)
                      font.family: root.bar ? root.bar.fontFamily : Style.font.family
                      font.pixelSize: Style.font.caption
                    }

                    MouseArea {
                      id: ctlArea
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: {
                        var key = parent.modelData.key
                        if (key === "resume") root.resumeSession(row.modelData)
                        else if (key === "delete") root.requestDelete()
                        else root.runAction(key, row.modelData)
                      }
                    }
                  }
                }
              }
            }

            Column {
              id: rowAge
              anchors.right: parent.right
              anchors.rightMargin: Style.space(16)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(1)

              Text {
                anchors.right: parent.right
                visible: String(row.modelData.tokensLabel || "") !== ""
                text: String(row.modelData.tokensLabel || "")
                textFormat: Text.PlainText
                color: root.dimmed
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: Style.font.caption
              }

              Text {
                anchors.right: parent.right
                text: root.fmtAge(modelData.mtime)
                color: root.dimmed
                font.family: root.bar ? root.bar.fontFamily : Style.font.family
                font.pixelSize: Style.font.bodySmall
              }
            }

            MouseArea {
              id: rowArea
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onPositionChanged: function(mouse) {
                if (!root.hoverArmed) return
                var g = rowArea.mapToGlobal(mouse.x, mouse.y)
                if (g.x !== root.lastMouseX || g.y !== root.lastMouseY) {
                  root.lastMouseX = g.x
                  root.lastMouseY = g.y
                  root.selectedIndex = row.index
                }
              }
              onClicked: root.resumeSession(row.modelData)
            }
          }
        }

        // ---- Empty state.
        Text {
          width: parent.width - Style.space(32)
          x: Style.space(16)
          visible: !root.scanning && root.shown.length === 0
          text: "Nothing here yet — sessions appear after you use an agent."
          color: root.dimmed
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.bodySmall
        }

        PanelSeparator { width: parent.width }

        // ---- Footer: key hints.
        Item {
          width: parent.width
          height: footerHints.implicitHeight + Style.space(14)

          Text {
            id: footerHints
            anchors.right: parent.right
            anchors.rightMargin: Style.space(16)
            anchors.verticalCenter: parent.verticalCenter
            text: "↵ resume · p peek · o folder · y copy · d delete ×2 · ←/→ agent · r rescan · esc"
            color: root.dimmed
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
