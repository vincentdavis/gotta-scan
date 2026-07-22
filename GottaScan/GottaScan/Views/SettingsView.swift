import SwiftUI

struct SettingsView: View {
    @AppStorage("uploadEndpoint") private var uploadEndpoint = ""

    var body: some View {
        Form {
            Section {
                TextField("https://example.com/api/scans", text: $uploadEndpoint)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } header: {
                Text("Web app upload")
            } footer: {
                Text("Scans are uploaded as a ZIP of the full scan bundle (images with embedded depth and gravity metadata, plus scan metadata). The processing pipeline behind this endpoint is a separate project.")
            }

            Section {
                LabeledContent("App", value: "GOTTA.SCAN")
                LabeledContent("Version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—")
            } header: {
                Text("About")
            } footer: {
                Text("Generic 3D capture app. Object-specific analysis (bike, foot, …) happens in the web app after upload.")
            }
        }
        .navigationTitle("Settings")
    }
}
