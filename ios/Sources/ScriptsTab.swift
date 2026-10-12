import SwiftUI

struct ScriptsTab: View {
    @State private var kind = 0

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("Kind", selection: $kind) {
                    Text("AutoHotkey").tag(0)
                    Text("Userscripts").tag(1)
                }
                .pickerStyle(.segmented)
                .padding(.horizontal)
                .padding(.vertical, 8)
                .accessibilityIdentifier("scriptKind")
                if kind == 0 { AhkList() } else { UserscriptList() }
            }
            .navigationTitle("Scripts")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

// MARK: AutoHotkey

struct AhkList: View {
    @State private var entries: [J] = []
    @State private var pins: [J] = []
    @State private var modes: [String] = []
    @State private var error = ""
    @State private var search = ""
    @State private var showOff = false
    @State private var busy = ""
    @State private var loaded = false

    private var shown: [J] {
        entries.filter { e in
            let running = e.s("state") == "running"
            if !search.isEmpty { return haystack(e).localizedCaseInsensitiveContains(search) }
            return running != showOff
        }
    }

    private func haystack(_ e: J) -> String {
        ([e.s("name"), e.s("id")] + e.arr("hotkeys").flatMap { [$0.s("hotkey"), $0.s("description"), $0.s("context")] })
            .joined(separator: " ")
    }

    var body: some View {
        List {
            Section {
                ErrorRow(text: error)
                TextField("Search scripts and hotkeys", text: $search)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("ahkSearch")
                Picker("State", selection: $showOff) {
                    Text("Running (\(entries.filter { $0.s("state") == "running" }.count))").tag(false)
                    Text("Off (\(entries.filter { $0.s("state") != "running" }.count))").tag(true)
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("ahkState")
                if !pins.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack {
                            ForEach(pins, id: \.pinKey) { pin in
                                Button(pin.s("label")) { Task { await runPin(pin) } }
                                    .buttonStyle(.bordered)
                                    .accessibilityIdentifier("pin-" + pin.s("label"))
                            }
                        }
                    }
                }
            }
            Section {
                ForEach(shown, id: \.idKey) { entry in
                    AhkRow(entry: entry, modes: modes, busy: busy == entry.s("id")) { action in
                        Task { await act(action, entry.s("id")) }
                    }
                }
                if loaded && shown.isEmpty { Text("No matches").foregroundColor(.secondary) }
            }
        }
        .refreshable { await load() }
        .task { if !loaded { await load() } }
    }

    @MainActor
    private func apply(_ json: J) {
        if json["entries"] != nil { entries = json.arr("entries") }
        if json["pins"] != nil { pins = json.arr("pins") }
        if json["mode_labels"] != nil { modes = json.strs("mode_labels") }
        error = json.ok ? "" : json.why
        loaded = true
    }

    @MainActor
    private func load() async { apply(await API.get("/api/ahk")) }

    @MainActor
    private func act(_ action: String, _ id: String) async {
        busy = id
        apply(await API.post("/api/ahk/action", ["action": action, "id": id]))
        busy = ""
    }

    @MainActor
    private func runPin(_ pin: J) async {
        let id = pin.s("id")
        let running = entries.contains { $0.s("id").lowercased() == id.lowercased() && $0.s("state") == "running" }
        await act(running && pin.b("restart_if_running") ? "restart" : "start", id)
    }
}

extension Dictionary where Key == String, Value == Any {
    var idKey: String { s("id") }
    var pinKey: String { s("label") + s("id") }
    var fileKey: String { s("file") + s("name") }
    var timeKey: String { s("t") + s("key") }
}

struct AhkRow: View {
    let entry: J
    let modes: [String]
    let busy: Bool
    let act: (String) -> Void
    @State private var open = false

    var body: some View {
        let id = entry.s("id")
        let running = entry.s("state") == "running"
        let keys = entry.arr("hotkeys")
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Circle().fill(running ? Color.green : Color.gray).frame(width: 10, height: 10)
                Text(entry.s("name")).font(.body)
                Spacer()
                if busy { ProgressView() }
            }
            Text(keys.isEmpty ? "No hotkeys found" : keys.prefix(3).map { $0.s("hotkey") }.joined(separator: " | "))
                .font(.caption).foregroundColor(.secondary).lineLimit(1)
            HStack(spacing: 10) {
                if running {
                    Button("Restart") { act("restart") }.accessibilityIdentifier("ahk-restart-" + entry.s("name"))
                    Button("Stop") { act("stop") }.tint(.red).accessibilityIdentifier("ahk-stop-" + entry.s("name"))
                } else {
                    Button("Start") { act("start") }.tint(.green).accessibilityIdentifier("ahk-start-" + entry.s("name"))
                }
                Button(open ? "Hide keys" : "Keys (\(keys.count))") { open.toggle() }
                    .accessibilityIdentifier("ahk-keys-" + entry.s("name"))
            }
            .buttonStyle(.bordered).controlSize(.small).disabled(busy)
            if entry.b("fkmodes") && !modes.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        ForEach(Array(modes.enumerated()), id: \.offset) { index, label in
                            Button(label) { act("mode\(index + 1)") }
                                .accessibilityIdentifier("ahk-mode-\(index + 1)")
                        }
                    }
                    .buttonStyle(.bordered).controlSize(.small)
                }
            }
            if open {
                ForEach(Array(keys.enumerated()), id: \.offset) { _, key in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(key.s("hotkey")).font(.callout.monospaced()).foregroundColor(.accentColor)
                        if !key.s("description").isEmpty { Text(key.s("description")).font(.caption) }
                        if !key.s("context").isEmpty && key.s("context") != "always" {
                            Text("when: " + key.s("context")).font(.caption2).foregroundColor(.secondary)
                        }
                    }
                }
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("ahk-" + entry.s("name"))
        .accessibilityValue(running ? "running" : "off")
        .id(id)
    }
}

// MARK: Userscripts (Tampermonkey)

struct UserscriptList: View {
    @State private var scripts: [J] = []
    @State private var settings: J = [:]
    @State private var labels: J = [:]
    @State private var connected = false
    @State private var hasSettings = false
    @State private var master: Bool?
    @State private var error = ""
    @State private var search = ""
    @State private var loaded = false
    @State private var newChar = ""
    @State private var newMix = ""
    @State private var matches: [J] = []
    @State private var matchKind = ""

    private var toggleKeys: [String] {
        settings.keys.filter { settings[$0] is Bool }.sorted { label($0) < label($1) }
    }

    private func label(_ key: String) -> String {
        let text = labels.s(key)
        return text.isEmpty ? key : text
    }

    private var shown: [J] {
        scripts.filter { s in
            search.isEmpty || ([s.s("name"), s.s("description"), s.s("note")] + s.strs("keys") + s.strs("found_keys") + s.strs("sites"))
                .joined(separator: " ").localizedCaseInsensitiveContains(search)
        }
    }

    var body: some View {
        List {
            Section(header: Text("Auto-Format on import"),
                    footer: Text(hasSettings ? (connected ? "A browser tab is listening. Changes apply within a few seconds."
                                                : "No browser tab is open right now. Changes are kept and applied when one opens.")
                                 : "No browser tab has reported its settings yet. Open one with the script and pull to refresh.")) {
                ErrorRow(text: error)
                HStack {
                    Text("Auto format")
                    Spacer()
                    Text(master == nil ? "?" : (master! ? "ON" : "OFF")).foregroundColor(.secondary)
                        .accessibilityIdentifier("afState")
                    Button("On") { Task { await setMaster("on") } }.accessibilityIdentifier("afOn")
                    Button("Off") { Task { await setMaster("off") } }.accessibilityIdentifier("afOff")
                }
                .buttonStyle(.bordered).controlSize(.small)
                ForEach(toggleKeys, id: \.self) { key in
                    Toggle(label(key), isOn: Binding(
                        get: { (settings[key] as? Bool) ?? false },
                        set: { value in Task { await setValues([key: value]) } }))
                        .accessibilityIdentifier("tm-" + key)
                }
                if hasSettings {
                    pickerRow("New character", text: $newChar, kind: "chars")
                    pickerRow("New mix", text: $newMix, kind: "mixes")
                    ForEach(Array(matches.prefix(30).enumerated()), id: \.offset) { _, match in
                        Button(match.s("label")) { Task { await pick(match) } }
                    }
                }
            }
            Section(header: Text("Scripts (\(scripts.count))")) {
                TextField("Search scripts and hotkeys", text: $search)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("tmSearch")
                ForEach(shown, id: \.fileKey) { script in
                    UserscriptRow(script: script)
                }
                if loaded && shown.isEmpty { Text("No matches").foregroundColor(.secondary) }
            }
        }
        .refreshable { await load() }
        .task { if !loaded { await load() } }
    }

    private func pickerRow(_ title: String, text: Binding<String>, kind: String) -> some View {
        HStack {
            TextField(title, text: text).textInputAutocapitalization(.never).autocorrectionDisabled()
            Button("Find") { Task { await find(kind, text.wrappedValue) } }
            Button("Set") {
                Task { await setValues(kind == "chars" ? ["newChar": text.wrappedValue] : ["newMix": text.wrappedValue, "newMixExpansion": ""]) }
            }
        }
        .buttonStyle(.bordered).controlSize(.small)
    }

    @MainActor
    private func applySettings(_ json: J) {
        if let cfg = json["cfg"] as? J {
            settings = cfg
            hasSettings = true
            newChar = cfg.s("newChar")
            newMix = cfg.s("newMix")
        }
        if json["connected"] != nil { connected = json.b("connected") }
    }

    @MainActor
    private func load() async {
        let json = await API.get("/api/userscripts")
        if json.ok {
            scripts = json.arr("scripts")
            labels = json.obj("toggle_labels")
            applySettings(json.obj("settings"))
            let af = json.obj("autoformat")
            master = af["state"] as? Bool
            error = ""
        } else {
            error = json.why
        }
        loaded = true
    }

    @MainActor
    private func setMaster(_ state: String) async {
        let json = await API.post("/api/ahk/af", ["state": state])
        if json["state"] != nil { master = json["state"] as? Bool } else { error = json.why }
    }

    @MainActor
    private func setValues(_ values: J) async {
        let json = await API.post("/api/userscripts/settings", body: values)
        if json["cfg"] is J { applySettings(json); error = "" } else { error = json.why.isEmpty ? "Setting was not applied" : json.why }
    }

    @MainActor
    private func find(_ kind: String, _ query: String) async {
        let json = await API.get("/api/userscripts/search", ["kind": kind, "q": query])
        matchKind = kind
        // matches are plain names for characters and {label, expansion} for mixes
        if let names = json["matches"] as? [String] {
            matches = names.map { name in ["label": name] as J }
        } else {
            matches = json.arr("matches")
        }
        if !json.ok { error = json.why }
    }

    @MainActor
    private func pick(_ match: J) async {
        if matchKind == "chars" {
            await setValues(["newChar": match.s("label")])
        } else {
            await setValues(["newMix": match.s("label"), "newMixExpansion": match.s("expansion")])
        }
        matches = []
    }
}

struct UserscriptRow: View {
    let script: J
    @State private var open = false

    var body: some View {
        let keys = script.strs("keys")
        let found = script.strs("found_keys")
        VStack(alignment: .leading, spacing: 4) {
            Button {
                open.toggle()
            } label: {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(script.s("name")).font(.body).foregroundColor(.primary)
                        Text(summary(keys, found)).font(.caption).foregroundColor(.secondary).lineLimit(1)
                    }
                    Spacer()
                    Image(systemName: open ? "chevron.up" : "chevron.down").font(.caption).foregroundColor(.secondary)
                }
                .contentShape(Rectangle())      // the empty middle of the row is tappable too
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("us-" + script.s("name"))
            if open {
                if !script.s("note").isEmpty { Text(script.s("note")).font(.caption) }
                else if !script.s("description").isEmpty { Text(script.s("description")).font(.caption) }
                ForEach(keys, id: \.self) { key in
                    Text(key).font(.callout.monospaced()).foregroundColor(.accentColor)
                }
                if !found.isEmpty {
                    Text("Named in the script: " + found.joined(separator: ", "))
                        .font(.caption).foregroundColor(.secondary)
                }
                Text("v\(script.s("version")) · \(script.s("modified")) · \(script.strs("sites").joined(separator: ", "))")
                    .font(.caption2).foregroundColor(.secondary)
            }
        }
        .padding(.vertical, 2)
    }

    private func summary(_ keys: [String], _ found: [String]) -> String {
        if !keys.isEmpty { return keys.prefix(3).joined(separator: " | ") }
        if !found.isEmpty { return found.prefix(4).joined(separator: " | ") }
        return "No hotkeys listed"
    }
}
