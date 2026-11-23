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
    @StateObject private var reconstructor = Reconstructor()

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
                Text("The full-quality reconstruction runs in the GOTTA.SCAN Mac app. This preview only checks coverage before you leave the object.")
            }

            Section {
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
            } header: {
                Text("Export")
            } footer: {
                Text("Share the ZIP to your Mac (AirDrop), then run reconstruction, exports, and measurements in the GOTTA.SCAN Mac app.")
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

    private func byteString(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}
