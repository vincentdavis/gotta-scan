import Foundation
import RealityKit

/// On-device preview reconstruction via PhotogrammetrySession (reduced detail).
/// The authoritative high-quality reconstruction happens later in the web app pipeline;
/// this exists so the user can sanity-check coverage before leaving the object.
@MainActor
final class Reconstructor: ObservableObject {
    enum Status: Equatable {
        case idle
        case running(progress: Double)
        case done(URL)
        case failed(String)
    }

    @Published private(set) var status: Status = .idle
    private var session: PhotogrammetrySession?
    private var task: Task<Void, Never>?
    private var userCancelled = false

    var isRunning: Bool {
        if case .running = status { return true }
        return false
    }

    func start(scan: ScanRecord, store: ScanStore) {
        guard !isRunning, !store.activeReconstructions.contains(scan.id) else { return }
        task?.cancel()
        userCancelled = false
        status = .running(progress: 0)
        task = Task { await run(scan: scan, store: store) }
    }

    func cancel() {
        userCancelled = true
        // PhotogrammetrySession.cancel() is the documented way to stop RealityKit's
        // reconstruction work; cancelling only the consuming Task leaves it running.
        session?.cancel()
        task?.cancel()
        task = nil
        status = .idle
    }

    private func run(scan: ScanRecord, store: ScanStore) async {
        store.activeReconstructions.insert(scan.id)
        // Reconstruct into a staging file; the existing model.usdz survives unless we succeed.
        let stagingURL = scan.modelsURL.appendingPathComponent("model-in-progress.usdz")
        defer {
            session = nil
            try? FileManager.default.removeItem(at: stagingURL)
            store.activeReconstructions.remove(scan.id)
        }
        do {
            try FileManager.default.createDirectory(at: scan.modelsURL, withIntermediateDirectories: true)
            try? FileManager.default.removeItem(at: stagingURL)

            var configuration = PhotogrammetrySession.Configuration()
            configuration.checkpointDirectory = scan.snapshotsURL

            let session = try PhotogrammetrySession(input: scan.imagesURL, configuration: configuration)
            self.session = session
            try session.process(requests: [.modelFile(url: stagingURL, detail: .reduced)])

            // outputs is a long-lived stream that does NOT end after .processingComplete
            // (a session supports multiple process() batches) — exit the loop explicitly.
            outputs: for try await output in session.outputs {
                switch output {
                case .requestProgress(_, let fractionComplete):
                    if !userCancelled {
                        status = .running(progress: fractionComplete)
                    }
                case .requestComplete(_, let result):
                    if case .modelFile(let url) = result {
                        try? FileManager.default.removeItem(at: scan.modelURL)
                        try FileManager.default.moveItem(at: url, to: scan.modelURL)
                        status = .done(scan.modelURL)
                    }
                case .requestError(_, let error):
                    status = .failed(String(describing: error))
                    break outputs
                case .processingComplete:
                    if case .running = status {
                        status = .failed("Reconstruction produced no model.")
                    }
                    break outputs
                case .processingCancelled:
                    status = .idle
                    break outputs
                default:
                    break
                }
            }
        } catch {
            status = userCancelled ? .idle : .failed(error.localizedDescription)
        }
        if userCancelled {
            status = .idle
        }
    }
}
