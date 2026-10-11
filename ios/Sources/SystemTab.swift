import SwiftUI

struct SystemTab: View {
    @State private var sys: J = [:]
    @State private var usage: J = [:]
    @State private var error = ""
    @State private var countsBusy = false
    @State private var usageBusy = false
    @State private var loaded = false

    var body: some View {
        NavigationStack {
            List {
                Section(header: Text("Now")) {
                    ErrorRow(text: error)
                    Meter(title: "CPU", percent: sys.d("cpu"), detail: "")
                    Meter(title: "RAM", percent: sys.d("ram_percent"),
                          detail: sys.has("ram_used_gb") ? "\(sys.s("ram_used_gb")) / \(sys.s("ram_total_gb")) GB" : "")
                    Meter(title: "Page file", percent: sys.d("swap_percent"), detail: "")
                    line("Disk activity", "read \(rate(sys.obj("disk_io").d("read_bytes"))) · write \(rate(sys.obj("disk_io").d("write_bytes")))")
                    line("Network", "down \(rate(sys.obj("net_io").d("bytes_recv"))) · up \(rate(sys.obj("net_io").d("bytes_sent")))")
                    if !sys.s("boot").isEmpty { line("Running since", sys.s("boot")) }
                }
                Section(header: Text("Disk space")) {
                    ForEach(sys.arr("disks"), id: \.mountKey) { disk in
                        Meter(title: disk.s("mount"), percent: disk.d("percent"),
                              detail: "\(disk.s("free_gb")) GB free of \(disk.s("total_gb"))")
                    }
                }
                Section(header: Text("Drive health"), footer: Text("Read from CrystalDiskInfo's last check of each drive.")) {
                    ForEach(sys.arr("smart"), id: \.nameKey) { drive in
                        DriveRow(drive: drive)
                    }
                    if loaded && sys.arr("smart").isEmpty { Text("No drive data").foregroundColor(.secondary) }
                }
                Section(header: Text("Biggest memory users")) {
                    ForEach(sys.arr("top"), id: \.pidKey) { proc in
                        line(proc.s("name"), megabytes(proc.d("ram_mb")))
                    }
                }
                Section(header: Text("Counts"), footer: Text(sys.obj("counts").s("at").isEmpty ? "Not counted yet." : "Counted \(sys.obj("counts").s("at")).")) {
                    ForEach(sys.obj("counts").arr("items"), id: \.labelKey) { item in
                        line(item.s("label"), item.has("count") ? grouped(item.i("count")) : (item.s("error").isEmpty ? "-" : item.s("error")))
                    }
                    Button(countsBusy ? "Counting..." : "Count now") { Task { await load(counts: true) } }
                        .disabled(countsBusy)
                        .accessibilityIdentifier("countNow")
                }
                Section(header: Text("Usage")) {
                    ForEach(usage.obj("fields").keys.sorted(), id: \.self) { key in
                        line(key, usage.obj("fields").s(key))
                    }
                    if !usage.isEmpty && !usage.ok { Text(usage.why).font(.footnote).foregroundColor(.red) }
                    Button(usageBusy ? "Loading..." : "Load usage") { Task { await loadUsage() } }
                        .disabled(usageBusy)
                        .accessibilityIdentifier("loadUsage")
                }
            }
            .navigationTitle("System")
            .refreshable { await load(counts: false) }
            .task { await load(counts: false) }
        }
    }

    private func line(_ title: String, _ value: String) -> some View {
        HStack {
            Text(title)
            Spacer()
            Text(value).foregroundColor(.secondary).multilineTextAlignment(.trailing)
        }
        .font(.callout)
    }

    private func grouped(_ value: Int?) -> String {
        guard let value = value else { return "-" }
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: value)) ?? "\(value)"
    }

    @MainActor
    private func load(counts: Bool) async {
        if counts { countsBusy = true }
        let json = await API.get("/api/system", counts ? ["counts": "1"] : [:])
        if json.ok { sys = json; error = "" } else { error = json.why }
        countsBusy = false
        loaded = true
    }

    @MainActor
    private func loadUsage() async {
        usageBusy = true
        usage = await API.get("/api/usage")
        usageBusy = false
    }
}

extension Dictionary where Key == String, Value == Any {
    var mountKey: String { s("mount") }
    var pidKey: String { s("pid") + s("name") }
    var labelKey: String { s("label") }
}

struct Meter: View {
    let title: String
    let percent: Double?
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(title)
                Spacer()
                Text(percent.map { String(format: "%.0f%%", $0) } ?? "-").foregroundColor(.secondary)
            }
            .font(.callout)
            ProgressView(value: min(max(percent ?? 0, 0), 100), total: 100)
                .tint((percent ?? 0) >= 90 ? Color.red : ((percent ?? 0) >= 75 ? Color.orange : Color.green))
            if !detail.isEmpty { Text(detail).font(.caption).foregroundColor(.secondary) }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("meter-" + title)
    }
}

struct DriveRow: View {
    let drive: J

    var body: some View {
        let health = drive.s("health")
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(drive.s("name")).font(.callout).lineLimit(1)
                Spacer()
                Text(health).font(.callout.bold())
                    .foregroundColor(health == "Good" ? .green : (health == "Caution" ? .orange : .red))
            }
            Text(detail).font(.caption).foregroundColor(.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("drive-" + drive.s("name"))
    }

    private var detail: String {
        var parts: [String] = []
        if let t = drive.i("temp_c") { parts.append("\(t) °C") }
        if let life = drive.i("life") { parts.append("life \(life)%") }
        if let n = drive.i("reallocated"), n > 0 { parts.append("\(n) reallocated") }
        if let n = drive.i("pending"), n > 0 { parts.append("\(n) pending") }
        if let n = drive.i("uncorrectable"), n > 0 { parts.append("\(n) uncorrectable") }
        if !drive.s("date").isEmpty { parts.append("read " + drive.s("date")) }
        return parts.joined(separator: " · ")
    }
}
