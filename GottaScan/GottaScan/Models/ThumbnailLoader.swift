import UIKit
import ImageIO

enum ThumbnailLoader {
    /// Loads a downsampled thumbnail from the scan's first captured image.
    /// The directory listing and decode both run off the main thread.
    static func thumbnail(for scan: ScanRecord, maxPixelSize: CGFloat) async -> UIImage? {
        let imagesURL = scan.imagesURL
        return await Task.detached(priority: .utility) {
            let imageExtensions = ["heic", "heif", "jpg", "jpeg", "png"]
            let firstImage = ((try? FileManager.default.contentsOfDirectory(
                at: imagesURL,
                includingPropertiesForKeys: nil,
                options: [.skipsHiddenFiles]
            )) ?? [])
                .filter { imageExtensions.contains($0.pathExtension.lowercased()) }
                .sorted { $0.lastPathComponent < $1.lastPathComponent }
                .first
            guard let firstImage else { return nil }

            let options: [CFString: Any] = [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
            ]
            guard let source = CGImageSourceCreateWithURL(firstImage as CFURL, nil),
                  let cgImage = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
                return nil
            }
            return UIImage(cgImage: cgImage)
        }.value
    }
}
