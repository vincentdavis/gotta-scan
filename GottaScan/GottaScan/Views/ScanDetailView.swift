import SwiftUI

/// Identifiable wrapper so a zipped export can drive a sheet presentation.
struct ExportedZip: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}

struct ScanDetailView: View {
    let scan: ScanRecord

    @EnvironmentObject private var store: ScanStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @StateObject private var reconstructor = Reconstructor()
    @StateObject private var uploader = Uploader()
    @StateObject private var resultService = ResultService()

    @AppStorage("uploadEndpoint") private var uploadEndpoint = ""
    @AppStorage("uploadToken") private var uploadToken = ""

    @State private var showModelPreview = false
    @State private var exportedZip: ExportedZip?
    @State private var lastExportURL: URL?
    @State private var isExporting = false
    @State private var exportError: String?
    @State private var folderSize: Int64?

    var body: some View {
        List {
            Section {
                HStack {
                    ScanThumbnailView(scan: scan)
                        .frame(width: 84, height: 84)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                    VStack(alignment: .leading, spacing: 6) {
                        Text(scan.metadata.name)
                            .font(.headline)
                        Text(scan.metadata.createdAt, format: .dateTime.year().month().day().hour().minute())
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text("\(scan.metadata.imageCount) images · \(scan.metadata.captureMode) mode · \(folderSize.map(byteString) ?? "…")")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Section {
                switch reconstructor.status {
                case .idle:
                    if store.activeReconstructions.contains(scan.id) {
                        // A build started from a previous visit to this screen is still running.
                        ProgressView("Reconstruction in progress…")
                    } else if scan.hasModel {
                        Button("View 3D model") { showModelPreview = true }
                        Button("Rebuild preview model") { reconstructor.start(scan: scan, store: store) }
                    } else {
                        Button("Build preview model") { reconstructor.start(scan: scan, store: store) }
                            .disabled(scan.metadata.imageCount < 10)
                        if scan.metadata.imageCount < 10 {
                            Text("Need at least ~10 images to reconstruct.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                case .running(let progress):
                    VStack(alignment: .leading, spacing: 6) {
                        ProgressView(value: progress)
                        HStack {
                            Text("Reconstructing… \(Int(progress * 100))%")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Button("Cancel") { reconstructor.cancel() }
                                .font(.caption)
                        }
                    }
                case .done:
                    Button("View 3D model") { showModelPreview = true }
                        .onAppear { store.refresh() }
                case .failed(let message):
                    VStack(alignment: .leading, spacing: 6) {
                        Label("Reconstruction failed", systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red)
                        Text(message)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Button("Try again") { reconstructor.start(scan: scan, store: store) }
                    }
                }
            } header: {
                Text("Preview model (on-device, reduced detail)")
            } footer: {
                Text("The full-quality reconstruction happens in the web app pipeline. This preview only checks coverage.")
            }

            Section("Export") {
                if isExporting {
                    ProgressView("Preparing ZIP…")
                } else {
                    Button {
                        exportZip()
                    } label: {
                        Label("Export scan bundle (ZIP)", systemImage: "square.and.arrow.up")
                    }
                }
                if let exportError {
                    Text(exportError)
                        .font(.caption)
                        .foregroundStyle(.red)
                }
            }

            Section("Upload") {
                if uploadEndpoint.isEmpty {
                    Text("Set an upload endpoint in Settings first.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else if store.activeUploads.contains(scan.id), uploader.status == .idle {
                    // An upload started from a previous visit to this screen is still running.
                    ProgressView("Uploading…")
                } else {
                    switch uploader.status {
                    case .idle, .failed:
                        Button {
                            upload()
                        } label: {
                            Label("Upload to web app", systemImage: "icloud.and.arrow.up")
                        }
                        if case .failed(let message) = uploader.status {
                            Text(message)
                                .font(.caption)
                                .foregroundStyle(.red)
                        }
                    case .zipping:
                        ProgressView("Compressing…")
                    case .uploading:
                        ProgressView("Uploading…")
                    case .done:
                        Label("Uploaded", systemImage: "checkmark.icloud")
                            .foregroundStyle(.green)
                    }
                }
                if let uploadedAt = scan.metadata.uploadedAt {
                    Text("Last uploaded \(uploadedAt.formatted(.dateTime.month().day().hour().minute()))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if showServerSection {
                Section("On the server") {
                    serverResultContent
                }
            }

            Section {
                Button(role: .destructive) {
                    store.delete(scan)
                    dismiss()
                } label: {
                    Label("Delete scan", systemImage: "trash")
                }
            }
        }
        .navigationTitle(scan.metadata.name)
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showModelPreview) {
            QuickLookPreview(url: scan.modelURL)
                .ignoresSafeArea()
        }
        .sheet(item: $exportedZip, onDismiss: {
            if let lastExportURL {
                try? FileManager.default.removeItem(at: lastExportURL)
                self.lastExportURL = nil
            }
        }) { item in
            ShareSheet(items: [item.url])
        }
        .task(id: scan.id) {
            let folderURL = scan.folderURL
            folderSize = await Task.detached(priority: .utility) {
                ScanRecord.folderSizeBytes(of: folderURL)
            }.value
        }
        .task(id: scan.metadata.uploadedAt) {
            if scan.metadata.uploadedAt != nil { fetchServerResult() }
        }
        .onChange(of: uploader.status) { _, newStatus in
            // A just-finished upload won't be reflected in this view's immutable
            // scan copy, so trigger the server fetch directly on completion.
            if newStatus == .done { fetchServerResult() }
        }
    }

    private var showServerSection: Bool {
        !uploadEndpoint.isEmpty && (scan.metadata.uploadedAt != nil || uploader.status == .done)
    }

    @ViewBuilder
    private var serverResultContent: some View {
        switch resultService.state {
        case .idle, .loading:
            HStack(spacing: 8) {
                ProgressView()
                Text("Checking status…").font(.caption).foregroundStyle(.secondary)
            }
        case .loaded(let result):
            if result.isComplete {
                if let dims = result.dimensionsText {
                    Label(dims, systemImage: "ruler")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Button {
                    openURL(result.bestLink)
                } label: {
                    Label("View 3D model on server", systemImage: "safari")
                }
                ShareLink(item: result.detailURL) {
                    Label("Share link", systemImage: "square.and.arrow.up")
                }
            } else if result.isFailed {
                Label(result.errorMessage ?? "Processing failed on the server.", systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.red)
                shareDetailLink(result.detailURL)
            } else {
                HStack(spacing: 8) {
                    ProgressView()
                    Text("Processing on the server…").font(.caption).foregroundStyle(.secondary)
                }
                Button("Refresh") { fetchServerResult() }.font(.caption)
                shareDetailLink(result.detailURL)
            }
        case .failed(let message):
            Text(message).font(.caption).foregroundStyle(.secondary)
            Button("Retry") { fetchServerResult() }.font(.caption)
        }
    }

    @ViewBuilder
    private func shareDetailLink(_ url: URL) -> some View {
        ShareLink(item: url) {
            Label("Share link", systemImage: "square.and.arrow.up")
        }
    }

    private func fetchServerResult() {
        guard !uploadEndpoint.isEmpty else { return }
        resultService.fetch(uploadEndpoint: uploadEndpoint, scanID: scan.id)
    }

    private func exportZip() {
        isExporting = true
        exportError = nil
        let folderURL = scan.folderURL
        Task {
            do {
                let zipURL = try await ZipExporter.zip(folder: folderURL)
                lastExportURL = zipURL
                exportedZip = ExportedZip(url: zipURL)
            } catch {
                exportError = error.localizedDescription
            }
            isExporting = false
        }
    }

    private func upload() {
        guard let endpoint = URL(string: uploadEndpoint) else {
            return
        }
        uploader.upload(scan: scan, endpoint: endpoint, token: uploadToken, store: store)
    }

    private func byteString(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}
