import Foundation

/// Python `urllib.parse.unquote`: ASCII runs are percent-decoded as UTF-8 with replacement.
enum PercentDecoding {
    /// The decoded string; text without "%" comes back unchanged.
    static func unquote(_ s: String) -> String {
        guard s.unicodeScalars.contains("%") else { return s }
        var out = String.UnicodeScalarView()
        var ascii: [UInt8] = []
        for scalar in s.unicodeScalars {
            if scalar.isASCII {
                ascii.append(UInt8(scalar.value))
                continue
            }
            out.append(contentsOf: decodeRun(ascii).unicodeScalars)
            ascii.removeAll()
            out.append(scalar)
        }
        out.append(contentsOf: decodeRun(ascii).unicodeScalars)
        return String(out)
    }

    /// One ASCII run: %XX pairs become bytes, then UTF-8 with U+FFFD for bad bytes.
    private static func decodeRun(_ run: [UInt8]) -> String {
        guard run.contains(37) else { return String(decoding: run, as: UTF8.self) }
        var bytes: [UInt8] = []
        var index = 0
        while index < run.count {
            if run[index] == 37, index + 2 < run.count,
               let high = hex(run[index + 1]), let low = hex(run[index + 2]) {
                bytes.append(high << 4 | low)
                index += 3
            } else {
                bytes.append(run[index])
                index += 1
            }
        }
        return String(decoding: bytes, as: UTF8.self)
    }

    /// The value of one hex digit byte.
    private static func hex(_ byte: UInt8) -> UInt8? {
        switch byte {
        case 48...57: return byte - 48
        case 65...70: return byte - 55
        case 97...102: return byte - 87
        default: return nil
        }
    }
}
