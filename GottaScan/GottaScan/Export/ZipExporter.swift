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
    /// Each call writes to a unique temp path so concurrent exports of the same scan
    /// can't delete each other's archives. Callers own deleting the returned file.
    ///
    /// The ZIP is the scan bundle (images with embedded depth and gravity, plus
    /// metadata); share it to the Mac (AirDrop) and run reconstruction there.
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
