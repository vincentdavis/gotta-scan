import Foundation

struct ScanMetadata: Codable, Hashable {
    var name: String
    var createdAt: Date
    var completedAt: Date?
    var imageCount: Int
    var captureMode: String
}

struct ScanRecord: Identifiable, Hashable {
    let id: String
    let folderURL: URL
    var metadata: ScanMetadata

    var imagesURL: URL { folderURL.appendingPathComponent("Images", isDirectory: true) }
    var snapshotsURL: URL { folderURL.appendingPathComponent("Snapshots", isDirectory: true) }
    var modelsURL: URL { folderURL.appendingPathComponent("Models", isDirectory: true) }
    var modelURL: URL { modelsURL.appendingPathComponent("model.usdz") }

    var hasModel: Bool {
        FileManager.default.fileExists(atPath: modelURL.path)
    }

    var imageFileURLs: [URL] {
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: imagesURL,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        )) ?? []
        return urls
            .filter { ["heic", "heif", "jpg", "jpeg", "png"].contains($0.pathExtension.lowercased()) }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    /// Total on-disk size of a folder, in bytes. A deep synchronous walk —
    /// call from a background task, never from a SwiftUI body.
    static func folderSizeBytes(of folderURL: URL) -> Int64 {
        guard let enumerator = FileManager.default.enumerator(
            at: folderURL,
            includingPropertiesForKeys: [.totalFileAllocatedSizeKey, .fileAllocatedSizeKey]
        ) else { return 0 }
        var total: Int64 = 0
        for case let url as URL in enumerator {
            let values = try? url.resourceValues(forKeys: [.totalFileAllocatedSizeKey, .fileAllocatedSizeKey])
            total += Int64(values?.totalFileAllocatedSize ?? values?.fileAllocatedSize ?? 0)
        }
        return total
    }
}

@MainActor
final class ScanStore: ObservableObject {
    @Published private(set) var scans: [ScanRecord] = []
    /// Scan IDs with an upload/reconstruction in flight. Lives here (not in a view)
    /// so the state survives navigating away from a detail view and back.
    @Published var activeUploads: Set<String> = []
    @Published var activeReconstructions: Set<String> = []

    private let rootURL: URL
    private let metadataFileName = "metadata.json"

    init() {
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        rootURL = documents.appendingPathComponent("Scans", isDirectory: true)
        try? FileManager.default.createDirectory(at: rootURL, withIntermediateDirectories: true)
        refresh()
    }

    func refresh() {
        let folders = (try? FileManager.default.contentsOfDirectory(
            at: rootURL,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        )) ?? []
        scans = folders
            .filter { (try? $0.resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory == true }
            .map { folder in
                let metadata = loadMetadata(in: folder) ?? ScanMetadata(
                    name: folder.lastPathComponent,
                    createdAt: (try? folder.resourceValues(forKeys: [.creationDateKey]))?.creationDate ?? .now,
                    completedAt: nil,
                    imageCount: 0,
                    captureMode: "object"
                )
                return ScanRecord(id: folder.lastPathComponent, folderURL: folder, metadata: metadata)
            }
            .sorted { $0.metadata.createdAt > $1.metadata.createdAt }
    }

    func createScan(mode: String) throws -> ScanRecord {
        // Fixed locale/calendar (QA1480): user locale settings must not leak into
        // scan IDs, which become folder names and HTTP header values. The UUID
        // suffix rules out same-second collisions.
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        let suffix = UUID().uuidString.prefix(4).lowercased()
        let id = "scan-" + formatter.string(from: .now) + "-" + suffix
        let folder = rootURL.appendingPathComponent(id, isDirectory: true)
        let record = ScanRecord(
            id: id,
            folderURL: folder,
            metadata: ScanMetadata(
                name: id,
                createdAt: .now,
                completedAt: nil,
                imageCount: 0,
                captureMode: mode
            )
        )
        try FileManager.default.createDirectory(at: record.imagesURL, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: record.snapshotsURL, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: record.modelsURL, withIntermediateDirectories: true)
        save(metadata: record.metadata, in: folder)
        refresh()
        return record
    }

    /// Called when a capture session ends. Updates the image count; discards the scan if nothing was captured.
    func finalizeOrDiscard(_ scan: ScanRecord, completed: Bool) {
        var record = scan
        record.metadata.imageCount = record.imageFileURLs.count
        if record.metadata.imageCount == 0 {
            try? FileManager.default.removeItem(at: record.folderURL)
        } else {
            if completed {
                record.metadata.completedAt = .now
            }
            save(metadata: record.metadata, in: record.folderURL)
        }
        refresh()
    }

    func delete(_ scan: ScanRecord) {
        try? FileManager.default.removeItem(at: scan.folderURL)
        refresh()
    }

    private func loadMetadata(in folder: URL) -> ScanMetadata? {
        let url = folder.appendingPathComponent(metadataFileName)
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(ScanMetadata.self, from: data)
    }

    private func save(metadata: ScanMetadata, in folder: URL) {
        let url = folder.appendingPathComponent(metadataFileName)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        if let data = try? encoder.encode(metadata) {
            try? data.write(to: url, options: .atomic)
        }
    }
}
