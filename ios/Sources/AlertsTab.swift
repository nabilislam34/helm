import SwiftUI

struct LimitName: Identifiable {
    let key: String
    let title: String
    var id: String { key }
}

struct AlertsTab: View {
    @State private var alerts: [J] = []
    @State private var muted: [String] = []
    @State private var limits: J = [:]
    @State private var watched: [String] = []
    @State private var telegram = false
    @State private var error = ""
    @State private var note = ""
    @State private var loaded = false

    private let limitNames: [LimitName] = [
        LimitName(key: "ram_percent", title: "RAM above (%)"), LimitName(key: "disk_free_gb", title: "Disk free below (GB)"),
        LimitName(key: "proc_ram_mb", title: "One service above (MB)"), LimitName(key: "disk_temp_c", title: "Drive hotter than (°C)"),
        LimitName(key: "cooldown_min", title: "Repeat the same alert after (min)"),
    ]

    var body: some View {
        NavigationStack {
            List {
                Section(footer: Text(telegram ? "Alerts are also sent to your Telegram bot chat." : "Telegram is off, so alerts only show here.")) {
                    ErrorRow(text: error)
                    Button("Send a test alert") { Task { await test() } }.accessibilityIdentifier("testAlert")
                    if !note.isEmpty { Text(note).font(.footnote).accessibilityIdentifier("alertNote") }
                }
                Section(header: Text("Watched services"), footer: Text("A watched service that is down for two checks in a row raises an alert. Turn one off here to mute it.")) {
                    ForEach(watched, id: \.self) { name in
                        Toggle(name, isOn: Binding(
                            get: { !muted.contains(name) },
                            set: { on in
                                var next = muted.filter { $0 != name }
                                if !on { next.append(name) }
                                Task { await save(["muted": next]) }
                            }))
                            .accessibilityIdentifier("watch-" + name)
                    }
                    if loaded && watched.isEmpty { Text("No watched services").foregroundColor(.secondary) }
                }
                Section(header: Text("Limits")) {
                    ForEach(limitNames) { limit in
                        let key = limit.key
                        let title = limit.title
                        Stepper(value: Binding(
                            get: { limits.d(key) ?? 0 },
                            set: { value in Task { await save(["thresholds": [key: value]]) } }),
                            in: 0...100_000, step: step(key)) {
                            HStack {
                                Text(title).font(.callout)
                                Spacer()
                                Text(String(format: "%.0f", limits.d(key) ?? 0)).foregroundColor(.secondary)
                                    .accessibilityIdentifier("limit-" + key)
                            }
                        }
                    }
                }
                Section(header: Text("History")) {
                    ForEach(alerts, id: \.timeKey) { alert in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(alert.s("text")).font(.callout).foregroundColor(color(alert.s("level")))
                            Text(alert.s("time") + (alert.b("sent") ? " · sent" : " · not sent"))
                                .font(.caption2).foregroundColor(.secondary)
                        }
                    }
                    if loaded && alerts.isEmpty { Text("No alerts yet").foregroundColor(.secondary) }
                }
            }
            .navigationTitle("Alerts")
            .refreshable { await load() }
            .task { await load() }
        }
    }

    private func step(_ key: String) -> Double {
        key == "proc_ram_mb" ? 500 : (key == "cooldown_min" || key == "disk_free_gb" ? 5 : 1)
    }

    private func color(_ level: String) -> Color {
        level == "bad" ? .red : (level == "good" ? .green : .orange)
    }

    @MainActor
    private func apply(_ json: J) {
        if json["muted"] != nil { muted = json.strs("muted") }
        if json["thresholds"] != nil { limits = json.obj("thresholds") }
    }

    @MainActor
    private func load() async {
        let json = await API.get("/api/alerts")
        if json.ok {
            alerts = json.arr("alerts")
            telegram = json.b("telegram")
            apply(json)
            error = ""
            let over = await API.get("/api/overview")
            if over.ok { watched = over.arr("services").filter { $0.b("watch") }.map { $0.s("name") } }
        } else {
            error = json.why
        }
        loaded = true
    }

    @MainActor
    private func save(_ change: J) async {
        let json = await API.post("/api/alerts/config", body: change)
        if json.ok { apply(json); error = "" } else { error = json.why }
    }

    @MainActor
    private func test() async {
        let json = await API.post("/api/alerts/test")
        note = json.ok ? (json.b("sent") ? "Test alert sent to Telegram." : "Test alert saved, but Telegram did not accept it.") : json.why
        await load()
    }
}
