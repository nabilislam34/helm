import SwiftUI

struct PendingAction: Identifiable {
    let name: String
    let action: String
    var id: String { name + action }
}

struct ServicesTab: View {
    @Binding var showSettings: Bool
    @Environment(\.scenePhase) private var scenePhase
    @State private var services: [J] = []
    @State private var system: J = [:]
    @State private var error = ""
    @State private var search = ""
    @State private var downOnly = false
    @State private var busy: Set<String> = []
    @State private var pending: PendingAction?
    @State private var note = ""

    private var shown: [J] {
        services.filter { row in
            (!downOnly || !row.b("running")) &&
                (search.isEmpty || row.s("name").localizedCaseInsensitiveContains(search) || row.s("group").localizedCaseInsensitiveContains(search))
        }
    }

    private var groups: [String] {
        var seen: [String] = []
        for row in shown where !seen.contains(row.s("group")) { seen.append(row.s("group")) }
        return seen
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ErrorRow(text: error)
                    Text(summary).font(.footnote).foregroundColor(.secondary).accessibilityIdentifier("summary")
                    if !note.isEmpty {
                        Text(note).font(.footnote).accessibilityIdentifier("note")
                    }
                    Toggle("Down only", isOn: $downOnly).accessibilityIdentifier("downOnly")
                }
                ForEach(groups, id: \.self) { group in
                    Section(header: Text(group)) {
                        ForEach(shown.filter { $0.s("group") == group }, id: \.nameKey) { row in
                            ServiceRow(row: row, busy: busy.contains(row.s("name"))) { action in
                                if action == "start" {
                                    Task { await run(row.s("name"), action) }
                                } else {
                                    pending = PendingAction(name: row.s("name"), action: action)
                                }
                            }
                        }
                    }
                }
            }
            .searchable(text: $search)
            .navigationTitle("Services")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityIdentifier("openSettings")
                }
            }
            .refreshable { await load() }
            .confirmationDialog(pending.map { "\($0.action.capitalized) \($0.name)?" } ?? "", isPresented: Binding(
                get: { pending != nil }, set: { if !$0 { pending = nil } }), titleVisibility: .visible) {
                if let p = pending {
                    Button(p.action.capitalized, role: .destructive) { Task { await run(p.name, p.action) } }
                        .accessibilityIdentifier("confirmAction")
                }
                Button("Cancel", role: .cancel) {}
            }
            // refreshes only while this tab is on screen and the app is in front
            .task {
                while !Task.isCancelled {
                    if scenePhase == .active { await load() }
                    try? await Task.sleep(nanoseconds: 10_000_000_000)
                }
            }
        }
    }

    private var summary: String {
        let up = services.filter { $0.b("running") }.count
        var text = "\(up) running, \(services.count - up) down"
        if let cpu = system.d("cpu"), let ram = system.d("ram_percent") {
            text += String(format: " · CPU %.0f%% · RAM %.0f%%", cpu, ram)
        }
        return text
    }

    @MainActor
    private func load() async {
        let json = await API.get("/api/overview")
        if json.ok {
            services = json.arr("services")
            system = json.obj("system")
            error = ""
        } else {
            error = json.why
        }
    }

    @MainActor
    private func run(_ name: String, _ action: String) async {
        busy.insert(name)
        let json = await API.post("/api/service", ["name": name, "action": action])
        busy.remove(name)
        note = json.ok ? "\(action) \(name): done" : "\(action) \(name): \(json.why)"
        await load()
    }
}

extension Dictionary where Key == String, Value == Any {
    var nameKey: String { s("name") }
}

struct ServiceRow: View {
    let row: J
    let busy: Bool
    let act: (String) -> Void

    var body: some View {
        let name = row.s("name")
        let running = row.b("running")
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Circle().fill(color).frame(width: 10, height: 10)
                Text(name).font(.body)
                if row.b("watch") {
                    Image(systemName: row.b("muted") ? "bell.slash" : "bell").font(.caption).foregroundColor(.secondary)
                }
                Spacer()
                if busy { ProgressView() }
            }
            Text(detail).font(.caption).foregroundColor(.secondary)
            HStack(spacing: 10) {
                if running {
                    Button("Restart") { act("restart") }.accessibilityIdentifier("restart-" + name)
                    Button("Stop") { act("stop") }.tint(.red).accessibilityIdentifier("stop-" + name)
                } else if row.b("can_start") {
                    Button("Start") { act("start") }.tint(.green).accessibilityIdentifier("start-" + name)
                }
            }
            .buttonStyle(.bordered).controlSize(.small).disabled(busy)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("svc-" + name)
        .accessibilityValue(running ? "running" : "down")
    }

    private var color: Color {
        if !row.b("running") { return .gray }
        if row.has("port_ok") && !row.b("port_ok") { return .orange }
        return .green
    }

    private var detail: String {
        guard row.b("running") else { return row.b("can_start") ? "Not running" : "Not running (no start command)" }
        var parts = ["pid \(row.s("pid"))", megabytes(row.d("ram_mb"))]
        if let handles = row.i("handles") { parts.append("\(handles) handles") }
        if let cpu = row.d("cpu") { parts.append(String(format: "CPU %.1f%%", cpu)) }
        parts.append("up " + duration(row.i("uptime_s")))
        if let port = row.i("port") {
            parts.append(row.has("port_ok") ? (row.b("port_ok") ? "port \(port) ok" : "port \(port) NOT answering") : "port \(port)")
        }
        return parts.joined(separator: " · ")
    }
}
