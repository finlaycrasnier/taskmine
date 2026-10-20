import Foundation

// Python differs from ICU in three ways the rules touch: `\s` also covers U+001C-U+001F;
// under (?i) an ASCII letter never matches two characters ("ss" vs "ß"); and i also matches "İ", "ı".
/// Rewrites an ICU rule pattern so NSRegularExpression matches like Python `re`, the core's engine.
enum PatternTranslation {
    private static let spaceItems = "\\s\\x{1c}-\\x{1f}"
    private static let dottedI = "\\x{130}\\x{131}"

    /// The pattern with Python semantics; `caseInsensitive` also applies to a leading `(?i)` flag group.
    static func python(_ pattern: String, caseInsensitive: Bool) -> String {
        let chars = Array(pattern.unicodeScalars)
        let ci = caseInsensitive || leadingFlags(chars).contains("i")
        var out = ""
        var i = 0
        while i < chars.count {
            let c = chars[i]
            if c == "\\" {
                let (escape, next) = readEscape(chars, i)
                out += outsideEscape(escape)
                i = next
            } else if c == "[" {
                let (set, next) = readSet(chars, i, ci)
                out += set
                i = next
            } else if c == "(" && i + 1 < chars.count && chars[i + 1] == "?" {
                let (group, next) = readGroupHead(chars, i)
                out += group
                i = next
            } else {
                out += ci ? letter(c) : String(c)
                i += 1
            }
        }
        return out
    }

    /// The flag letters of a leading `(?flags)` or `(?flags:` group.
    private static func leadingFlags(_ chars: [Unicode.Scalar]) -> String {
        guard chars.count > 2, chars[0] == "(", chars[1] == "?" else { return "" }
        var flags = ""
        for c in chars[2...] {
            if c == ")" || c == ":" { return flags }
            guard c.properties.isAlphabetic || c == "-" else { return "" }
            flags.unicodeScalars.append(c)
        }
        return ""
    }

    /// An ASCII letter as a one-character class of its cases; other characters unchanged.
    private static func letter(_ c: Unicode.Scalar) -> String {
        guard c.isASCII, c.properties.isAlphabetic else { return String(c) }
        let lower = String(c).lowercased(), upper = String(c).uppercased()
        return "[" + lower + upper + (lower == "i" ? dottedI : "") + "]"
    }

    /// An escape outside a set: `\s` and `\S` widened, anything else as written.
    private static func outsideEscape(_ escape: String) -> String {
        switch escape {
        case "\\s": return "[" + spaceItems + "]"
        case "\\S": return "[^" + spaceItems + "]"
        default: return escape
        }
    }

    /// One escape sequence starting at `i` (a backslash), with its braces or hex digits.
    private static func readEscape(_ chars: [Unicode.Scalar], _ i: Int) -> (String, Int) {
        guard i + 1 < chars.count else { return ("\\", i + 1) }
        let kind = chars[i + 1]
        var end = i + 2
        if ["p", "P", "N", "x"].contains(kind), end < chars.count, chars[end] == "{" {
            while end < chars.count, chars[end] != "}" { end += 1 }
            end = min(end + 1, chars.count)
        } else if kind == "x" {
            end = min(i + 4, chars.count)
        } else if kind == "u" {
            end = min(i + 6, chars.count)
        } else if kind == "U" {
            end = min(i + 10, chars.count)
        }
        return (String(String.UnicodeScalarView(chars[i..<end])), end)
    }

    /// A group head such as `(?:`, `(?<!`, `(?i)` copied unchanged.
    private static func readGroupHead(_ chars: [Unicode.Scalar], _ i: Int) -> (String, Int) {
        var end = i + 2
        if end < chars.count, chars[end] == "<", end + 1 < chars.count, chars[end + 1] == "=" || chars[end + 1] == "!" {
            end += 2
        } else if end < chars.count, [":", "=", "!", ">"].contains(chars[end]) {
            end += 1
        } else {
            while end < chars.count, chars[end] != ")" && chars[end] != ":" { end += 1 }
            end = min(end + 1, chars.count)
        }
        return (String(String.UnicodeScalarView(chars[i..<end])), end)
    }

    /// A bracketed set with `\s` widened and, under case folding, "İ" and "ı" added when it holds i.
    private static func readSet(_ chars: [Unicode.Scalar], _ start: Int, _ ci: Bool) -> (String, Int) {
        var out = "["
        var i = start + 1
        if i < chars.count, chars[i] == "^" { out += "^"; i += 1 }
        if i < chars.count, chars[i] == "]" { out += "\\]"; i += 1 }
        var holdsI = false
        var previous: Unicode.Scalar?
        while i < chars.count, chars[i] != "]" {
            let c = chars[i]
            if c == "[" {
                let (nested, next) = readSet(chars, i, ci)
                out += nested
                i = next
                previous = nil
                continue
            }
            if c == "\\" {
                let (escape, next) = readEscape(chars, i)
                out += escape == "\\s" ? spaceItems : escape == "\\S" ? "[^" + spaceItems + "]" : escape
                previous = escape.unicodeScalars.count == 2 ? escape.unicodeScalars.last : nil
                i = next
                continue
            }
            if c == "-", let low = previous, i + 1 < chars.count, chars[i + 1] != "]" {
                let high = chars[i + 1] == "\\" ? readEscape(chars, i + 1).0.unicodeScalars.last! : chars[i + 1]
                holdsI = holdsI || covers(low, high)
            }
            holdsI = holdsI || c == "i" || c == "I"
            out.unicodeScalars.append(c)
            previous = c
            i += 1
        }
        if ci && holdsI {
            if out.hasSuffix("-") && !out.hasSuffix("\\-") { out = String(out.dropLast()) + "\\-" }
            out += dottedI
        }
        return (out + "]", min(i + 1, chars.count))
    }

    /// True when the range low-high holds i or I.
    private static func covers(_ low: Unicode.Scalar, _ high: Unicode.Scalar) -> Bool {
        (low.value...max(low.value, high.value)).contains(0x69) || (low.value...max(low.value, high.value)).contains(0x49)
    }
}
