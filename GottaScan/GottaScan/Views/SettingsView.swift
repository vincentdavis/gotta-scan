import SwiftUI

struct SettingsView: View {
    @AppStorage("uploadEndpoint") private var uploadEndpoint = ""
    @AppStorage("uploadToken") private var uploadToken = ""

    var body: some View {
        Form {
            Section {
                TextField("https://example.com/api/scans", text: $uploadEndpoint)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField("Upload token (optional)", text: $uploadToken)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } header: {
                Text("Web app upload")
            } footer: {
                Text("Scans upload as a ZIP of the full scan bundle (images with embedded depth and gravity, plus metadata). If the server is configured with an upload token, enter it here — it's sent with each upload so only authorized devices can post scans.")
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
