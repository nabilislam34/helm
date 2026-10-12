import SwiftUI

@main
struct HelmApp: App {
    init() { TestHooks.apply() }

    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

struct RootView: View {
    @AppStorage("server") private var server = ""
    @AppStorage("key") private var key = ""
    @State private var showSettings = false

    var body: some View {
        TabView {
            ServicesTab(showSettings: $showSettings)
                .tabItem { Label("Services", systemImage: "server.rack") }
            ScriptsTab()
                .tabItem { Label("Scripts", systemImage: "keyboard") }
            SystemTab()
                .tabItem { Label("System", systemImage: "gauge") }
            CommandsTab()
                .tabItem { Label("Commands", systemImage: "terminal") }
            AlertsTab()
                .tabItem { Label("Alerts", systemImage: "bell") }
        }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .onAppear { if server.isEmpty { showSettings = true } }
        // helm://setup?server=http://192.168.1.10:8775&key=abc fills in Settings from a link
        .onOpenURL { url in
            guard url.scheme == "helm", let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems else { return }
            for item in items {
                if item.name == "server", let v = item.value { server = v }
                if item.name == "key", let v = item.value { key = v }
            }
        }
    }
}

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage("server") private var server = ""
    @AppStorage("key") private var key = ""
    @State private var result = ""
    @State private var testing = false

    var body: some View {
        NavigationStack {
            Form {
                Section(header: Text("PC"), footer: Text("Address of the Helm service on your PC, for example 192.168.1.10:8775. The key is in config.json next to the service.")) {
                    TextField("192.168.1.10:8775", text: $server)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .accessibilityIdentifier("serverField")
                    SecureField("Key", text: $key)
                        .textContentType(.oneTimeCode)      // stops the iOS "Save Password?" prompt
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .accessibilityIdentifier("keyField")
                }
                Section {
                    Button(testing ? "Testing..." : "Test connection") { Task { await test() } }
                        .disabled(testing)
                        .accessibilityIdentifier("testConnection")
                    if !result.isEmpty {
                        Text(result).font(.footnote).accessibilityIdentifier("testResult")
                    }
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }.accessibilityIdentifier("settingsDone")
                }
            }
        }
    }

    @MainActor
    private func test() async {
        testing = true
        let ping = await API.get("/api/ping")
        if !ping.ok {
            result = ping.why
        } else {
            let over = await API.get("/api/overview")
            result = over.ok ? "Connected. \(over.arr("services").count) services." : over.why
        }
        testing = false
    }
}
