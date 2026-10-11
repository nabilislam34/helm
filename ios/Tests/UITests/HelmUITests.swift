import XCTest

/// Runs the real app in the simulator against Tests/mock_server.py (same answer shapes as the PC service).
/// Each test only reads state it changed itself, because the mock keeps its state for the whole run.
final class HelmUITests: XCTestCase {
    var app: XCUIApplication!
    private let server = "http://127.0.0.1:8775"

    override func setUp() {
        continueAfterFailure = false
    }

    // MARK: helpers

    private func launch(server: String? = "http://127.0.0.1:8775", key: String = "test") {
        app = XCUIApplication()
        var args = ["-uiTestReset"]
        if let server = server { args += ["-uiTestSet", "server=" + server, "-uiTestSet", "key=" + key] }
        app.launchArguments = args
        app.launch()
    }

    private func el(_ id: String) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: id).firstMatch
    }

    private func shot(_ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot())
        a.name = name
        a.lifetime = .keepAlways
        add(a)
    }

    /// Lists are lazy: a row far down does not exist until it is scrolled to.
    private func need(_ e: XCUIElement, _ what: String, swipes: Int = 8, file: StaticString = #filePath, line: UInt = #line) {
        if e.waitForExistence(timeout: 12) { return }
        for _ in 0..<swipes {
            app.swipeUp()
            if e.waitForExistence(timeout: 1.5) { return }
        }
        shot("MISSING " + what)
        XCTFail("missing: " + what, file: file, line: line)
    }

    private func waitFor(_ what: String, timeout: TimeInterval = 20, file: StaticString = #filePath, line: UInt = #line,
                         _ test: () -> Bool) {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if test() { return }
            usleep(250_000)
        }
        shot("WAIT " + what)
        XCTFail("never true: " + what, file: file, line: line)
    }

    private func tab(_ name: String) {
        let button = app.tabBars.buttons[name]
        XCTAssertTrue(button.waitForExistence(timeout: 15), "tab " + name)
        button.tap()
    }

    /// The confirm button of an action sheet, however this iOS version exposes it.
    private func confirm(_ id: String, title: String) {
        let byId = el(id)
        if byId.waitForExistence(timeout: 4) { byId.tap(); return }
        let inSheet = app.sheets.buttons[title]
        if inSheet.waitForExistence(timeout: 2) { inSheet.tap(); return }
        let all = app.buttons.matching(NSPredicate(format: "label == %@", title)).allElementsBoundByIndex
        if let last = all.last { last.tap(); return }
        shot("NO CONFIRM " + title)
        XCTFail("no confirm button: " + title)
    }

    private func flip(_ toggle: XCUIElement) {
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.93, dy: 0.5)).tap()
    }

    // MARK: tests

    func test01FirstRunSettings() {
        launch(server: nil)
        let field = el("serverField")
        need(field, "settings sheet on first run")
        field.tap()
        field.typeText("127.0.0.1:8775")
        let key = el("keyField")
        key.tap()
        key.typeText("test")
        el("testConnection").tap()
        let result = el("testResult")
        waitFor("connected message") { result.exists && result.label.contains("Connected. 3 services") }
        el("settingsDone").tap()
        need(el("svc-svc_up"), "services after setup")
        shot("services")
    }

    func test02WrongKeyShowsWhy() {
        launch(key: "nope")
        let error = el("error")
        waitFor("wrong key message") { error.exists && error.label.contains("Wrong key") }
    }

    func test03StartAndStopService() {
        launch()
        let down = el("svc-svc_down")
        need(down, "down service row")
        XCTAssertEqual(down.value as? String, "down")
        el("start-svc_down").tap()
        waitFor("svc_down running") { (self.el("svc-svc_down").value as? String) == "running" }
        waitFor("note says done") { self.el("note").exists && self.el("note").label.contains("start svc_down: done") }
        el("stop-svc_down").tap()
        confirm("confirmAction", title: "Stop")
        waitFor("svc_down down again") { (self.el("svc-svc_down").value as? String) == "down" }
        XCTAssertEqual(el("svc-svc_up").value as? String, "running", "the other service must be untouched")
        flip(el("downOnly"))
        waitFor("down only hides running rows") { !self.el("svc-svc_up").exists && self.el("svc-svc_down").exists }
    }

    func test04AhkScripts() {
        launch()
        tab("Scripts")
        let row = el("ahk-Alpha keys")
        need(row, "running AHK script")
        XCTAssertFalse(el("ahk-Beta").exists, "off script is on the Off list")
        el("ahk-keys-Alpha keys").tap()
        XCTAssertTrue(app.staticTexts["Ctrl+Alt+A"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["when: Everything"].exists)
        XCTAssertTrue(el("ahk-mode-5").exists, "mode buttons")
        shot("ahk")
        el("pin-Pin Beta").tap()
        waitFor("pinned script started") { self.el("ahk-Beta").exists }
        el("ahk-stop-Alpha keys").tap()
        waitFor("stopped script leaves the running list") { !self.el("ahk-Alpha keys").exists }
    }

    func test05Userscripts() {
        launch()
        tab("Scripts")
        app.buttons["Userscripts"].firstMatch.tap()
        let toggle = el("tm-cleanTags")
        need(toggle, "auto-format setting")
        XCTAssertEqual(toggle.value as? String, "1")
        XCTAssertTrue(app.staticTexts["Clean tags"].exists, "label from the PC")
        flip(toggle)
        waitFor("setting off after the PC answers") { (self.el("tm-cleanTags").value as? String) == "0" }
        el("afOff").tap()
        waitFor("master off") { self.el("afState").label == "OFF" }
        let script = el("us-Tool A")
        need(script, "script row")
        script.tap()
        need(app.staticTexts["Shift + F11 (Toggle)"], "cheatsheet key")
        need(app.staticTexts["hand note"], "cheatsheet note")
        shot("userscripts")
    }

    func test06System() {
        launch()
        tab("System")
        need(el("meter-RAM"), "ram meter")
        let bad = el("drive-DriveBad")
        need(bad, "drive row")
        XCTAssertTrue(bad.label.contains("Caution"), bad.label)
        XCTAssertTrue(bad.label.contains("1 pending"), bad.label)
        shot("system")
        let count = el("countNow")
        need(count, "count button")
        count.tap()
        need(app.staticTexts["1,234"], "count result")
        need(app.staticTexts["folder missing"], "count error text")
        let usage = el("loadUsage")
        need(usage, "usage button")
        usage.tap()
        need(app.staticTexts["2899"], "usage value")
    }

    func test07Commands() {
        launch()
        tab("Commands")
        let state = el("runState")
        waitFor("status loaded") { state.exists && (state.label == "Idle" || state.label == "Running") }
        XCTAssertTrue(el("logTail").label.contains("Done. OK:1"))
        let on = el("toggleOn-dryrun")
        need(on, "switch row")
        XCTAssertEqual(el("toggleState-dryrun").label, "?")
        on.tap()
        waitFor("switch reports ON") { self.el("toggleState-dryrun").label == "ON" }
        el("toggleOff-dryrun").tap()
        waitFor("switch reports OFF") { self.el("toggleState-dryrun").label == "OFF" }
        shot("commands")
    }

    func test08RunNeedsConfirm() {
        launch()
        tab("Commands")
        let run = el("cmd-run")
        need(run, "run button")
        run.tap()
        confirm("confirmCommand", title: "Send run")
        waitFor("running after confirm") { self.el("runState").label == "Running" }
        el("cmd-stop").tap()
        waitFor("idle after stop") { self.el("runState").label == "Idle" }
        let choice = el("choice-model-v5")
        need(choice, "model choice")
        choice.tap()
        need(app.staticTexts["PC says: v5"], "model reported by the PC", swipes: 1)
    }

    func test09Alerts() {
        launch()
        tab("Alerts")
        let watch = el("watch-svc_up")
        need(watch, "watched service")
        XCTAssertEqual(watch.value as? String, "1")
        flip(watch)
        waitFor("muted after the PC answers") { (self.el("watch-svc_up").value as? String) == "0" }
        el("testAlert").tap()
        waitFor("test alert note") { self.el("alertNote").exists && self.el("alertNote").label.contains("sent to Telegram") }
        need(app.staticTexts["Helm test alert"], "new alert in history")
        need(app.staticTexts["DOWN: svc_x is not running"], "older alert in history", swipes: 2)
        shot("alerts")
    }
}
