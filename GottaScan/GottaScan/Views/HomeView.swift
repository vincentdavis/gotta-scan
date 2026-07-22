import SwiftUI
import RealityKit

struct HomeView: View {
    @EnvironmentObject private var store: ScanStore
    @State private var activeScan: ScanRecord?
    @State private var showNewScanDialog = false
    @State private var scanCreationError: String?

    private var deviceSupported: Bool {
        ObjectCaptureSession.isSupported
    }

    var body: some View {
        NavigationStack {
            Group {
                if !deviceSupported {
                    UnsupportedDeviceView()
                } else if store.scans.isEmpty {
                    ContentUnavailableView(
                        "No scans yet",
                        systemImage: "cube.transparent",
                        description: Text("Tap New Scan to capture your first object.")
                    )
                } else {
                    scanList
                }
            }
            .navigationTitle("GOTTA.SCAN")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    NavigationLink {
                        SettingsView()
                    } label: {
                        Image(systemName: "gearshape")
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    if deviceSupported {
                        Button {
                            showNewScanDialog = true
                        } label: {
                            Label("New Scan", systemImage: "plus")
                        }
                    }
                }
            }
            .confirmationDialog("Capture mode", isPresented: $showNewScanDialog) {
                Button("Object — small item with bounding box") { startScan(.object) }
                Button("Area — region of a larger object") { startScan(.area) }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Object mode guides you around a single object. Area mode free-captures a region, e.g. part of a bike frame.")
            }
            .alert(
                "Couldn't create scan",
                isPresented: Binding(
                    get: { scanCreationError != nil },
                    set: { if !$0 { scanCreationError = nil } }
                )
            ) {
                Button("OK") { scanCreationError = nil }
            } message: {
                Text(scanCreationError ?? "")
            }
        }
        .fullScreenCover(item: $activeScan) { scan in
            CaptureFlowView(scan: scan) { completed in
                store.finalizeOrDiscard(scan, completed: completed)
                activeScan = nil
            }
        }
    }

    private var scanList: some View {
        List {
            ForEach(store.scans) { scan in
                NavigationLink(value: scan.id) {
                    ScanRow(scan: scan)
                }
            }
            .onDelete { indexSet in
                for index in indexSet {
                    store.delete(store.scans[index])
                }
            }
        }
        .navigationDestination(for: String.self) { scanID in
            if let scan = store.scans.first(where: { $0.id == scanID }) {
                ScanDetailView(scan: scan)
            } else {
                ContentUnavailableView("Scan not found", systemImage: "questionmark.circle")
            }
        }
    }

    private func startScan(_ style: CaptureStyle) {
        do {
            activeScan = try store.createScan(mode: style.rawValue)
        } catch {
            scanCreationError = error.localizedDescription
        }
    }
}

private struct ScanRow: View {
    let scan: ScanRecord

    var body: some View {
        HStack(spacing: 12) {
            ScanThumbnailView(scan: scan)
                .frame(width: 56, height: 56)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 4) {
                Text(scan.metadata.name)
                    .font(.headline)
                    .lineLimit(1)
                HStack(spacing: 8) {
                    Text(scan.metadata.createdAt, format: .dateTime.month().day().hour().minute())
                    Label("\(scan.metadata.imageCount)", systemImage: "camera")
                    if scan.hasModel {
                        Label("3D", systemImage: "cube")
                    }
                    if scan.metadata.uploadedAt != nil {
                        Image(systemName: "checkmark.icloud")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
    }
}

struct ScanThumbnailView: View {
    let scan: ScanRecord
    @State private var image: UIImage?

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(.secondarySystemBackground))
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "cube.transparent")
                    .foregroundStyle(.secondary)
            }
        }
        .task(id: scan.id) {
            image = await ThumbnailLoader.thumbnail(for: scan, maxPixelSize: 200)
        }
    }
}

struct UnsupportedDeviceView: View {
    var body: some View {
        ContentUnavailableView {
            Label("Device not supported", systemImage: "iphone.slash")
        } description: {
            Text("GOTTA.SCAN needs a LiDAR-equipped iPhone (iPhone Pro models, e.g. iPhone 17 Pro). Guided 3D capture is not available on this device.")
        }
    }
}
