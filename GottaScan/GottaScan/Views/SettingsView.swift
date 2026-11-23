import SwiftUI

struct SettingsView: View {
    var body: some View {
        Form {
            Section {
                LabeledContent("App", value: "GOTTA.SCAN")
                LabeledContent("Version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—")
            } header: {
                Text("About")
            } footer: {
                Text("Generic 3D capture app. Capture an object here, export the scan bundle (ZIP), and AirDrop it to the GOTTA.SCAN Mac app, which reconstructs the mesh and runs object-specific analysis (bike, foot, …).")
            }
        }
        .navigationTitle("Settings")
    }
}
