import CoreGraphics
import Foundation

/// Pixels of one window's page area, kept in memory only (reader spec 7.6, 7.7).
enum ScreenCapture {
    private typealias CreateImage = @convention(c) (CGRect, UInt32, UInt32, UInt32) -> Unmanaged<CGImage>?

    /// CGWindowListCreateImage, resolved at run time (no ScreenCaptureKit dependency).
    private static let createImage: CreateImage? = {
        guard let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "CGWindowListCreateImage") else { return nil }
        return unsafeBitCast(symbol, to: CreateImage.self)
    }()

    /// The on-screen window of `pid` whose bounds match `frame` within 8% of size (24 px) and 96 px of position.
    static func windowID(pid: pid_t, frame: CGRect) -> CGWindowID? {
        guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]] else { return nil }
        for info in list {
            guard (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
                  (info[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
                  let dict = info[kCGWindowBounds as String] as? NSDictionary,
                  let bounds = CGRect(dictionaryRepresentation: dict), matches(bounds, frame),
                  let number = info[kCGWindowNumber as String] as? NSNumber else { continue }
            return CGWindowID(number.uint32Value)
        }
        return nil
    }

    /// True when two frames agree within the tolerances of spec 7.6.
    static func matches(_ a: CGRect, _ b: CGRect) -> Bool {
        let dw = max(24, b.width * 0.08), dh = max(24, b.height * 0.08)
        return abs(a.width - b.width) <= dw && abs(a.height - b.height) <= dh
            && abs(a.minX - b.minX) <= 96 && abs(a.minY - b.minY) <= 96
    }

    /// The pixels of `rect` from window `id`, scaled so the longest side is at most 2048 px (and at most 2x).
    static func image(window id: CGWindowID, rect: CGRect) -> CGImage? {
        guard let createImage, rect.width >= 1, rect.height >= 1 else { return nil }
        let options = CGWindowImageOption.boundsIgnoreFraming.rawValue | CGWindowImageOption.bestResolution.rawValue
        guard let raw = createImage(rect, CGWindowListOption.optionIncludingWindow.rawValue, id, options)?
                .takeRetainedValue() else { return nil }
        let scale = min(2.0, 2048 / max(rect.width, rect.height))
        let width = Int((rect.width * scale).rounded()), height = Int((rect.height * scale).rounded())
        guard width > 0, height > 0,
              let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        context.interpolationQuality = .high
        context.draw(raw, in: CGRect(x: 0, y: 0, width: width, height: height))
        return context.makeImage()
    }
}
