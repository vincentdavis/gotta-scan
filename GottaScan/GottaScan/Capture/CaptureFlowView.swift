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
    @State private var passIndex = 1
    @State private var showCoverage = false

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

                // Coverage inspection: the accumulated LiDAR point cloud, so the
                // user can see which parts of the shape still have gaps.
                if showCoverage, case .capturing = session.state {
                    CoverageOverlay(session: session, onClose: { showCoverage = false })
                }

                CaptureOverlay(
                    session: session,
                    style: style,
                    passIndex: $passIndex,
                    showCoverage: $showCoverage,
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

/// Full-screen review of the captured point cloud, so the user can spot gaps
/// before finishing and go re-capture the thin/missing regions.
private struct CoverageOverlay: View {
    let session: ObjectCaptureSession
    let onClose: () -> Void

    var body: some View {
        ZStack {
            Color.black.opacity(0.92).ignoresSafeArea()
            VStack(spacing: 14) {
                Text("Coverage so far")
                    .font(.headline)
                    .foregroundStyle(.white)
                Text("Rotate to inspect. Sparse or empty areas still need more shots.")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.7))
                    .multilineTextAlignment(.center)
                ObjectCapturePointCloudView(session: session)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                Button("Resume capture", action: onClose)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
            }
            .padding(24)
        }
    }
}

/// State-driven controls layered over the ObjectCaptureView.
private struct CaptureOverlay: View {
    let session: ObjectCaptureSession
    let style: CaptureStyle
    @Binding var passIndex: Int
    @Binding var showCoverage: Bool
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
                if case .capturing = session.state {
                    Button {
                        showCoverage = true
                    } label: {
                        Label("Coverage", systemImage: "cube.transparent")
                    }
                    .buttonStyle(.bordered)
                    .tint(.white)
                }
                shotCounter
            }
            .padding()

            feedbackBanner
                .animation(.easeInOut(duration: 0.2), value: Self.feedbackMessage(session.feedback)?.text)

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

    /// Live coaching from ObjectCaptureSession.feedback — the single most useful
    /// signal, shown while capturing so bad shots are corrected as they happen.
    @ViewBuilder
    private var feedbackBanner: some View {
        if case .capturing = session.state, let message = Self.feedbackMessage(session.feedback) {
            Label(message.text, systemImage: message.symbol)
                .font(.headline)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(.ultraThinMaterial, in: Capsule())
                .foregroundStyle(.white)
                .padding(.top, 8)
        }
    }

    /// Highest-priority active feedback mapped to an icon + message.
    static func feedbackMessage(_ feedback: Set<ObjectCaptureSession.Feedback>) -> (symbol: String, text: String)? {
        if feedback.contains(.objectTooClose) { return ("arrow.up.left.and.arrow.down.right", "Move farther away") }
        if feedback.contains(.objectTooFar) { return ("arrow.down.right.and.arrow.up.left", "Move closer") }
        if feedback.contains(.environmentTooDark) { return ("lightbulb", "More light needed") }
        if feedback.contains(.environmentLowLight) { return ("lightbulb", "More light needed") }
        if feedback.contains(.movingTooFast) { return ("tortoise", "Slow down") }
        if feedback.contains(.outOfFieldOfView) { return ("viewfinder", "Aim at your object") }
        return nil
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
                    Text("Pass \(passIndex) complete. Add another angle, flip for the underside, or finish.")
                        .font(.callout)
                        .foregroundStyle(.white)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                    HStack(spacing: 12) {
                        Button {
                            // Another orbit at a different height, same bounding box.
                            session.beginNewScanPass()
                            passIndex += 1
                        } label: {
                            Label("Another angle", systemImage: "arrow.triangle.2.circlepath")
                        }
                        .buttonStyle(.bordered)
                        .tint(.white)

                        // Flipping captures the underside — the surface that would
                        // otherwise be an open hole in the reconstruction.
                        if !session.feedback.contains(.objectNotFlippable) {
                            Button {
                                session.beginNewScanPassAfterFlip()
                                passIndex += 1
                            } label: {
                                Label("Flip", systemImage: "arrow.uturn.up")
                            }
                            .buttonStyle(.bordered)
                            .tint(.white)
                        }
                    }
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
