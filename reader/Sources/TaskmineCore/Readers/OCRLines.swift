import CoreGraphics
import Foundation

/// Orders recognised lines into text (reader spec 7.6).
enum OCRLines {
    /// One recognised line and its box, top-left origin.
    struct Line {
        let text: String
        let box: CGRect
    }

    /// Lines top to bottom; one row when vertical centres differ by at most half a line height.
    /// Within a row, left to right: a gap wider than 0.6 times the larger height is ` | `, else a space.
    static func join(_ lines: [Line]) -> String {
        var rows: [[Line]] = []
        for line in lines.sorted(by: { $0.box.midY < $1.box.midY }) {
            if let last = rows.last?.last, abs(line.box.midY - last.box.midY) <= max(line.box.height, last.box.height) / 2 {
                rows[rows.count - 1].append(line)
            } else {
                rows.append([line])
            }
        }
        return rows.map(joinRow).joined(separator: "\n")
    }

    /// One row joined left to right.
    private static func joinRow(_ row: [Line]) -> String {
        var out = ""
        var previous: Line?
        for line in row.sorted(by: { $0.box.minX < $1.box.minX }) {
            if let previous {
                let gap = line.box.minX - previous.box.maxX
                out += gap > 0.6 * max(line.box.height, previous.box.height) ? " | " : " "
            }
            out += line.text
            previous = line
        }
        return out
    }
}
