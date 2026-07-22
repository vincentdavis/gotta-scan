import Foundation

enum ZipExportError: LocalizedError {
    case zipFailed

    var errorDescription: String? {
        switch self {
        case .zipFailed: return "Could not create ZIP archive."
        }
    }
}

enum ZipExporter {
    /// Zips a folder using NSFileCoordinator's forUploading option (no third-party dependency).
    /// Each call writes to a unique temp path so concurrent export/upload of the same scan
    /// can't delete each other's archives. Callers own deleting the returned file.
    static func zip(folder: URL) async throws -> URL {
        try await Task.detached(priority: .userInitiated) {
            var coordinatorError: NSError?
            var copyError: Error?
            var resultURL: URL?

            let coordinator = NSFileCoordinator()
            coordinator.coordinate(readingItemAt: folder, options: [.forUploading], error: &coordinatorError) { zipURL in
                do {
                    let destination = FileManager.default.temporaryDirectory
                        .appendingPathComponent("\(folder.lastPathComponent)-\(UUID().uuidString)")
                        .appendingPathExtension("zip")
                    try FileManager.default.copyItem(at: zipURL, to: destination)
                    resultURL = destination
                } catch {
                    copyError = error
                }
            }

            if let coordinatorError { throw coordinatorError }
            if let copyError { throw copyError }
            guard let resultURL else { throw ZipExportError.zipFailed }
            return resultURL
        }.value
    }
}

/// Uploads a zipped scan bundle to the configured web app endpoint.
/// Prototype: single foreground PUT. Known limitation for later: a background
/// URLSession (delegate-based) is required for multi-hundred-MB uploads to survive
/// the phone locking or the app being backgrounded.
@MainActor
final class Uploader: ObservableObject {
    enum Status: Equatable {
        case idle
        case zipping
        case uploading
        case done
        case failed(String)
    }

    @Published private(set) var status: Status = .idle

    /// The store is passed in so completion side effects (markUploaded, activeUploads)
    /// persist even if the initiating view has been dismissed mid-upload.
    func upload(scan: ScanRecord, endpoint: URL, store: ScanStore) {
        guard status != .zipping, status != .uploading else { return }
        guard !store.activeUploads.contains(scan.id) else { return }
        Task { await run(scan: scan, endpoint: endpoint, store: store) }
    }

    private func run(scan: ScanRecord, endpoint: URL, store: ScanStore) async {
        store.activeUploads.insert(scan.id)
        defer { store.activeUploads.remove(scan.id) }

        status = .zipping
        do {
            let zipURL = try await ZipExporter.zip(folder: scan.folderURL)
            defer { try? FileManager.default.removeItem(at: zipURL) }
            status = .uploading

            var request = URLRequest(url: endpoint)
            request.httpMethod = "PUT"
            request.setValue("application/zip", forHTTPHeaderField: "Content-Type")
            request.setValue(scan.id, forHTTPHeaderField: "X-Scan-Id")

            let (_, response) = try await URLSession.shared.upload(for: request, fromFile: zipURL)
            guard let httpResponse = response as? HTTPURLResponse,
                  (200..<300).contains(httpResponse.statusCode) else {
                let code = (response as? HTTPURLResponse)?.statusCode ?? -1
                status = .failed("Server returned status \(code).")
                return
            }
            store.markUploaded(scan)
            status = .done
        } catch {
            status = .failed(error.localizedDescription)
        }
    }
}
