// GOTTA.SCAN Mac worker: wraps macOS RealityKit PhotogrammetrySession as a CLI.
// Build: swiftc -O -parse-as-library PhotogrammetryCLI.swift -o photogrammetry-cli
// Usage: photogrammetry-cli <images-dir> <output.usdz> [preview|reduced|medium|full|raw] [obj-output-dir]
//
// When obj-output-dir is given, a second modelFile request with a directory URL
// makes PhotogrammetrySession write OBJ + MTL + texture maps there (macOS 12+),
// so no USDZ conversion step is ever needed downstream.

import Foundation
import RealityKit

@main
struct PhotogrammetryCLI {
    static func main() async {
        let args = CommandLine.arguments
        guard args.count >= 3 else {
            FileHandle.standardError.write(Data("usage: photogrammetry-cli <images-dir> <output.usdz> [detail] [obj-output-dir]\n".utf8))
            exit(64)
        }
        let inputURL = URL(fileURLWithPath: args[1], isDirectory: true)
        let outputURL = URL(fileURLWithPath: args[2])
        let detailName = args.count > 3 ? args[3] : "full"
        let objDirectory = args.count > 4 ? args[4] : nil

        let detail: PhotogrammetrySession.Request.Detail
        switch detailName {
        case "preview": detail = .preview
        case "reduced": detail = .reduced
        case "medium": detail = .medium
        case "full": detail = .full
        case "raw": detail = .raw
        default:
            FileHandle.standardError.write(Data("unknown detail level: \(detailName)\n".utf8))
            exit(64)
        }

        do {
            let configuration = PhotogrammetrySession.Configuration()
            let session = try PhotogrammetrySession(input: inputURL, configuration: configuration)

            var requests: [PhotogrammetrySession.Request] = [.modelFile(url: outputURL, detail: detail)]
            if let objDirectory {
                // isDirectory: true is load-bearing — a bare path URL makes the
                // session treat this as a (bogus) usdz file request.
                let objURL = URL(fileURLWithPath: objDirectory, isDirectory: true)
                try FileManager.default.createDirectory(at: objURL, withIntermediateDirectories: true)
                requests.append(.modelFile(url: objURL, detail: detail))
            }
            try session.process(requests: requests)

            // outputs never ends on its own after .processingComplete — exit explicitly.
            for try await output in session.outputs {
                switch output {
                case .requestProgress(_, let fractionComplete):
                    print(String(format: "progress %.3f", fractionComplete))
                case .inputComplete:
                    print("input complete")
                case .invalidSample(let id, let reason):
                    print("invalid sample \(id): \(reason)")
                case .automaticDownsampling:
                    print("automatic downsampling engaged")
                case .requestError(_, let error):
                    FileHandle.standardError.write(Data("request error: \(error)\n".utf8))
                    exit(1)
                case .requestComplete(_, let result):
                    if case .modelFile(let url) = result {
                        print("model written: \(url.path)")
                    }
                case .processingComplete:
                    print("processing complete")
                    exit(0)
                case .processingCancelled:
                    FileHandle.standardError.write(Data("processing cancelled\n".utf8))
                    exit(2)
                default:
                    break
                }
            }
        } catch {
            FileHandle.standardError.write(Data("failed: \(error)\n".utf8))
            exit(1)
        }
        FileHandle.standardError.write(Data("outputs stream ended without completion\n".utf8))
        exit(1)
    }
}
