import SwiftUI

struct CommandsTab: View {
    @AppStorage("commandTarget") private var target = ""
    @State private var targets: [String] = []
    @State private var toggles: [String] = []
    @State private var fields: [String] = []
    @State private var choices: J = [:]
    @State private var quick: [J] = []
    @State private var status: J = [:]
    @State private var known: [String: String] = [:]     // last value the PC reported per toggle
    @State private var output = ""
    @State private var error = ""
    @State private var busy = false
    @State private var confirm = ""
    @State private var fieldName = ""
    @State private var fieldValue = ""
    @State private var chars = ""
    @State private var exclude = ""
    @State private var loaded = false

    var body: some View {
        NavigationStack {
            List {
                topSection
                logSection
                charsSection
                choiceSections
                switchSection
                fieldSection
                excludeSection
                templateSection
                quickSection
            }
            .navigationTitle("Commands")
            .refreshable { await load() }
            .task { if !loaded { await load() } }
            .confirmationDialog("Send \(confirm) to \(target)?", isPresented: Binding(
                get: { !confirm.isEmpty }, set: { if !$0 { confirm = "" } }), titleVisibility: .visible) {
                Button("Send \(confirm)", role: confirm == "run" ? nil : .destructive) {
                    let cmd = confirm
                    Task { await send(["cmd": cmd]) }
                }
                .accessibilityIdentifier("confirmCommand")
                Button("Cancel", role: .cancel) {}
            }
        }
    }

    @ViewBuilder private var topSection: some View {
    Section {
        ErrorRow(text: error)
        if targets.count > 1 {
            Picker("Target", selection: $target) {
                ForEach(targets, id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("targetPicker")
            .onChange(of: target) { _ in
                known = [:]
                Task { await loadStatus() }
            }
        }
        HStack {
            Text(status.isEmpty ? "No status yet" : (status.b("running") ? "Running" : "Idle"))
                .accessibilityIdentifier("runState")
            Spacer()
            if status.has("queue") { Text("queue \(status.s("queue"))").foregroundColor(.secondary) }
            if busy { ProgressView() }
        }
        if !status.strs("chars").isEmpty {
            Text("Characters: " + status.strs("chars").joined(separator: ", ")).font(.caption)
        }
        HStack(spacing: 10) {
            Button("Run") { confirm = "run" }.tint(.green).accessibilityIdentifier("cmd-run")
            Button("Stop") { Task { await send(["cmd": "stop"]) } }.tint(.red).accessibilityIdentifier("cmd-stop")
            Button("Resume") { Task { await send(["cmd": "resume"]) } }.accessibilityIdentifier("cmd-resume")
            Button("Clear queue") { confirm = "clearqueue" }.accessibilityIdentifier("cmd-clearqueue")
        }
        .buttonStyle(.bordered).controlSize(.small).disabled(busy)
    }
    }

    @ViewBuilder private var logSection: some View {
    if !status.strs("log").isEmpty {
        Section(header: Text("Log")) {
            Text(status.strs("log").suffix(12).joined(separator: "\n"))
                .font(.caption2.monospaced())
                .accessibilityIdentifier("logTail")
        }
    }
    if !output.isEmpty {
        Section(header: Text("Last answer")) {
            Text(output).font(.caption.monospaced()).textSelection(.enabled)
                .accessibilityIdentifier("lastAnswer")
        }
    }
    }

    @ViewBuilder private var charsSection: some View {
    Section(header: Text("Characters")) {
        TextField("Names, comma separated", text: $chars)
            .textInputAutocapitalization(.never).autocorrectionDisabled()
            .accessibilityIdentifier("charsField")
        HStack(spacing: 10) {
            Button("Add") { Task { await send(["cmd": "addchar", "value": chars]); chars = "" } }
                .disabled(chars.trimmingCharacters(in: .whitespaces).isEmpty)
                .accessibilityIdentifier("cmd-addchar")
            Button("Clear list") { confirm = "clearchars" }
        }
        .buttonStyle(.bordered).controlSize(.small)
    }
    }

    @ViewBuilder private var choiceSections: some View {
    ForEach(choices.keys.sorted(), id: \.self) { name in
        Section(header: Text(name.capitalized), footer: Text(known[name].map { "PC says: \($0)" } ?? "")) {
            HStack(spacing: 10) {
                ForEach(choices.strs(name), id: \.self) { value in
                    Button(value) { Task { await send(["cmd": "toggle", "name": name, "value": value], track: name) } }
                        .accessibilityIdentifier("choice-\(name)-\(value)")
                }
                Button("Show") { Task { await send(["cmd": "toggle", "name": name, "value": "status"], track: name) } }
            }
            .buttonStyle(.bordered).controlSize(.small)
        }
    }
    }

    @ViewBuilder private var switchSection: some View {
    Section(header: Text("Switches"), footer: Text("The PC only reports a switch after it is set, so each one shows ? until you tap On or Off.")) {
        ForEach(toggles, id: \.self) { name in
            HStack {
                Text(name)
                Spacer()
                Text(known[name] ?? "?").foregroundColor(.secondary)
                    .accessibilityIdentifier("toggleState-" + name)
                Button("On") { Task { await send(["cmd": "toggle", "name": name, "value": true], track: name) } }
                    .accessibilityIdentifier("toggleOn-" + name)
                Button("Off") { Task { await send(["cmd": "toggle", "name": name, "value": false], track: name) } }
                    .accessibilityIdentifier("toggleOff-" + name)
            }
            .buttonStyle(.bordered).controlSize(.small)
        }
    }
    }

    @ViewBuilder private var fieldSection: some View {
    if !fields.isEmpty {
        Section(header: Text("Set a value")) {
            Picker("Field", selection: $fieldName) {
                ForEach(fields, id: \.self) { Text($0).tag($0) }
            }
            .accessibilityIdentifier("fieldPicker")
            HStack {
                TextField("Value", text: $fieldValue).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("fieldValue")
                Button("Set") { Task { await send(["cmd": "set", "name": fieldName, "value": fieldValue]) } }
                    .buttonStyle(.bordered).controlSize(.small)
                    .disabled(fieldName.isEmpty || fieldValue.isEmpty)
                    .accessibilityIdentifier("cmd-set")
            }
        }
    }
    }

    @ViewBuilder private var excludeSection: some View {
    Section(header: Text("Skip sources with these labels")) {
        TextField("label, label", text: $exclude).textInputAutocapitalization(.never).autocorrectionDisabled()
        HStack(spacing: 10) {
            Button("Set") { Task { await send(["cmd": "exclude", "labels": exclude]) } }
                .disabled(exclude.trimmingCharacters(in: .whitespaces).isEmpty)
            Button("Off") { Task { await send(["cmd": "exclude", "labels": "off"]) } }
            Button("Show") { Task { await send(["cmd": "exclude", "labels": ""]) } }
        }
        .buttonStyle(.bordered).controlSize(.small)
    }
    }

    @ViewBuilder private var templateSection: some View {
    Section(header: Text("Template")) {
        HStack(spacing: 10) {
            Button("Show") { Task { await send(["cmd": "gettemplate"]) } }
            Button("Saved list") { Task { await send(["cmd": "listtemplates"]) } }
            Button("Clear") { confirm = "cleartemplate" }.tint(.red)
        }
        .buttonStyle(.bordered).controlSize(.small)
    }
    }

    @ViewBuilder private var quickSection: some View {
    if !quick.isEmpty {
        Section(header: Text("Quick actions")) {
            ForEach(quick, id: \.nameKey) { item in
                Button(item.s("label")) { Task { await runQuick(item.s("name")) } }
                    .accessibilityIdentifier("quick-" + item.s("name"))
            }
        }
    }
    }

    @MainActor
    private func load() async {
        let json = await API.get("/api/targets")
        if json.ok {
            targets = json.strs("targets")
            toggles = json.strs("toggles")
            fields = json.strs("fields")
            choices = json.obj("choices")
            quick = json.arr("quick")
            if !targets.contains(target) { target = targets.first ?? "" }
            if !fields.contains(fieldName) { fieldName = fields.first ?? "" }
            error = ""
            await loadStatus()
        } else {
            error = json.why
        }
        loaded = true
    }

    @MainActor
    private func loadStatus() async {
        guard !target.isEmpty else { return }
        let json = await API.post("/api/cmd", body: ["target": target, "cmd": "status"])
        if json["running"] != nil { status = json; error = "" } else { status = [:]; error = json.why }
    }

    /// Sends one command. `track` names the switch whose reported value should be remembered.
    @MainActor
    private func send(_ command: J, track: String? = nil) async {
        busy = true
        var body = command
        body["target"] = target
        let json = await API.post("/api/cmd", body: body)
        output = pretty(json)
        if let name = track, json[name] != nil {
            if let flag = json[name] as? Bool, !(json[name] is String) {
                known[name] = flag ? "ON" : "OFF"
            } else {
                known[name] = json.s(name)
            }
        }
        busy = false
        if command.s("cmd") != "status" { await loadStatus() }
    }

    @MainActor
    private func runQuick(_ name: String) async {
        busy = true
        let json = await API.post("/api/quick", ["name": name])
        output = json.ok ? "Started (pid \(json.s("pid")))" : json.why
        busy = false
    }
}
