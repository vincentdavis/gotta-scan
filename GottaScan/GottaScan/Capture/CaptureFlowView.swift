import SwiftUI
import RealityKit

/// Which capture style to use. Object mode uses the bounding-box detection flow;
/// area mode skips detection and free-captures a region (good for e.g. a frame junction).
enum CaptureStyle: String, CaseIterable, Identifiable {
    case object
    case area

    var id: String { rawValue }
    var label: String {
        switch self {
        case .object: return "Object"
        case .area: return "Area"
        }
    }
}

/// Full-screen guided capture flow backed by RealityKit's ObjectCaptureSession.
struct CaptureFlowView: View {
    let scan: ScanRecord
    let onFinished: (_ completed: Bool) -> Void

    @State private var session: ObjectCaptureSession?
    @State private var style: CaptureStyle
    @State private var errorMessage: String?
    @State private var didFinish = false

    init(scan: ScanRecord, onFinished: @escaping (_ completed: Bool) -> Void) {
        self.scan = scan
        self.onFinished = onFinished
        _style = State(initialValue: CaptureStyle(rawValue: scan.metadata.captureMode) ?? .object)
    }

    var body: some View {
        ZStack {
            if let session {
                ObjectCaptureView(session: session)
                    .ignoresSafeArea()
                CaptureOverlay(
                    session: session,
                    style: style,
                    onCancel: { cancel() }
                )
            } else {
                Color.black.ignoresSafeArea()
                ProgressView("Starting capture…")
                    .tint(.white)
                    .foregroundStyle(.white)
            }
        }
        .task { await startSession() }
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        .onDisappear { UIApplication.shared.isIdleTimerDisabled = false }
        .alert(
            "Capture failed",
            isPresented: Binding(
                get: { errorMessage != nil },
                set: { if !$0 { errorMessage = nil } }
            )
        ) {
            Button("OK") {
                errorMessage = nil
                finish(false)
            }
        } message: {
            Text(errorMessage ?? "")
        }
    }

    /// Idempotent completion: the Cancel button and the stateUpdates loop can race,
    /// so onFinished must only ever fire once.
    private func finish(_ completed: Bool) {
        guard !didFinish else { return }
        didFinish = true
        onFinished(completed)
    }

    private func startSession() async {
        guard session == nil else { return }
        let newSession = ObjectCaptureSession()
        var configuration = ObjectCaptureSession.Configuration()
        configuration.checkpointDirectory = scan.snapshotsURL
        configuration.isOverCaptureEnabled = true
        newSession.start(imagesDirectory: scan.imagesURL, configuration: configuration)
        session = newSession

        for await state in newSession.stateUpdates {
            switch state {
            case .completed:
                finish(true)
                return
            case .failed(let error):
                // A user-initiated cancel also surfaces as .failed(.cancelled);
                // didFinish is already set in that path, so don't show an error for it.
                if !didFinish {
                    errorMessage = String(describing: error)
                }
                return
            default:
                break
            }
        }
    }

    private func cancel() {
        session?.cancel()
        finish(false)
    }
}

/// State-driven controls layered over the ObjectCaptureView.
private struct CaptureOverlay: View {
    let session: ObjectCaptureSession
    let style: CaptureStyle
    let onCancel: () -> Void

    var body: some View {
        VStack {
            HStack {
                // No Cancel during .finishing/.completed: cancelling there races the
                // imminent .completed state and would discard a finished capture.
                if showsCancel {
                    Button("Cancel", action: onCancel)
                        .buttonStyle(.bordered)
                        .tint(.white)
                }
                Spacer()
                shotCounter
            }
            .padding()

            Spacer()

            bottomControls
                .padding(.bottom, 30)
        }
    }

    private var showsCancel: Bool {
        switch session.state {
        case .initializing, .ready, .detecting, .capturing:
            return true
        default:
            return false
        }
    }

    @ViewBuilder
    private var shotCounter: some View {
        if case .capturing = session.state {
            Label("\(session.numberOfShotsTaken)", systemImage: "camera.fill")
                .font(.headline.monospacedDigit())
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(.ultraThinMaterial, in: Capsule())
        }
    }

    @ViewBuilder
    private var bottomControls: some View {
        switch session.state {
        case .ready:
            VStack(spacing: 12) {
                Text(style == .object
                     ? "Point the camera at the object, then continue."
                     : "Point the camera at the region you want to capture.")
                    .font(.callout)
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
                Button(style == .object ? "Continue" : "Start Capture") {
                    if style == .object {
                        _ = session.startDetecting()
                    } else {
                        session.startCapturing()
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
        case .detecting:
            VStack(spacing: 12) {
                Text("Adjust the bounding box around the object.")
                    .font(.callout)
                    .foregroundStyle(.white)
                HStack(spacing: 16) {
                    Button("Reset") { session.resetDetection() }
                        .buttonStyle(.bordered)
                        .tint(.white)
                    Button("Start Capture") { session.startCapturing() }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                }
            }
        case .capturing:
            VStack(spacing: 12) {
                if session.userCompletedScanPass {
                    Text("Scan pass complete — finish, or keep capturing for more coverage.")
                        .font(.callout)
                        .foregroundStyle(.white)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }
                Button("Finish") { session.finish() }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
            }
        case .finishing:
            ProgressView("Finishing…")
                .tint(.white)
                .foregroundStyle(.white)
        default:
            EmptyView()
        }
    }
}
