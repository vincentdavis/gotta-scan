import Foundation

/// The processed-scan status the web app reports back, with absolute links the
/// app can open in Safari or hand to a share sheet.
struct ScanResult: Decodable, Equatable {
    let scanID: String
    let status: String
    let hasModel: Bool
    let imageCount: Int
    let detailURL: URL
    let viewerURL: URL?
    let modelUSDZURL: URL?
    let modelGLBURL: URL?
    let dimensionsMM: [Double]?
    let printWatertight: Bool?
    let errorMessage: String?

    enum CodingKeys: String, CodingKey {
        case scanID = "scan_id"
        case status
        case hasModel = "has_model"
        case imageCount = "image_count"
        case detailURL = "detail_url"
        case viewerURL = "viewer_url"
        case modelUSDZURL = "model_usdz_url"
        case modelGLBURL = "model_glb_url"
        case dimensionsMM = "dimensions_mm"
        case printWatertight = "print_watertight"
        case errorMessage = "error_message"
    }

    var isComplete: Bool { status == "complete" }
    var isFailed: Bool { status == "failed" }

    /// Best link to open: the interactive 3D viewer once ready, else the detail page.
    var bestLink: URL { viewerURL ?? detailURL }

    var dimensionsText: String? {
        guard let d = dimensionsMM, d.count == 3 else { return nil }
        return String(format: "%.0f × %.0f × %.0f mm", d[0], d[1], d[2])
    }
}

/// Fetches a scan's processed status from the web app's status endpoint,
/// derived from the same base URL the app uploads to.
@MainActor
final class ResultService: ObservableObject {
    enum State: Equatable {
        case idle
        case loading
        case loaded(ScanResult)
        case failed(String)
    }

    @Published private(set) var state: State = .idle

    /// The server origin (scheme://host:port) taken from the configured upload endpoint,
    /// so the status request goes to the same server the bundle was uploaded to.
    static func serverBase(fromUploadEndpoint endpoint: String) -> URL? {
        guard var components = URLComponents(string: endpoint), components.host != nil else { return nil }
        components.path = ""
        components.query = nil
        components.fragment = nil
        return components.url
    }

    func fetch(uploadEndpoint: String, scanID: String) {
        guard let base = Self.serverBase(fromUploadEndpoint: uploadEndpoint) else {
            state = .failed("Set a valid upload endpoint in Settings first.")
            return
        }
        let statusURL = base
            .appendingPathComponent("api")
            .appendingPathComponent("scans")
            .appendingPathComponent(scanID)
            .appendingPathComponent("status")
        state = .loading
        Task { await load(statusURL) }
    }

    private func load(_ url: URL) async {
        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 15
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                state = .failed("No response from server.")
                return
            }
            if http.statusCode == 404 {
                state = .failed("This scan isn't on the server — upload it first.")
                return
            }
            guard (200..<300).contains(http.statusCode) else {
                state = .failed("Server returned status \(http.statusCode).")
                return
            }
            state = .loaded(try JSONDecoder().decode(ScanResult.self, from: data))
        } catch {
            state = .failed(error.localizedDescription)
        }
    }
}
