import Foundation
import CoreText
import CoreGraphics

// Compare native and converted fonts through the same renderer. Pixel differences
// are reported, not hidden behind a claim of cross-platform pixel equivalence.
let url = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
let descriptors = CTFontManagerCreateFontDescriptorsFromURL(url as CFURL) as! [CTFontDescriptor]
precondition(descriptors.count == 24)
var faces: [[String: Any]] = []
var samples: [[String: Any]] = []
for descriptor in descriptors {
    let converted = CTFontCreateWithFontDescriptor(descriptor, 1000, nil)
    precondition((CTFontCopyAttribute(converted, kCTFontURLAttribute) as! URL).standardizedFileURL == url)
    let name = CTFontCopyPostScriptName(converted) as String
    let native = CTFontCreateWithName(name as CFString, 1000, nil)
    precondition(CTFontCopyPostScriptName(native) as String == name)
    let version = CTFontCopyName(native, kCTFontVersionNameKey)! as String
    precondition(CTFontCopyName(converted, kCTFontVersionNameKey)! as String == version)
    let count = CTFontGetGlyphCount(native)
    precondition(CTFontGetGlyphCount(converted) == count)
    var maxAdvanceError: CGFloat = 0, maxBoundsError: CGFloat = 0
    for i in 0..<count {
        var glyph = CGGlyph(i), a = CGSize.zero, b = CGSize.zero
        CTFontGetAdvancesForGlyphs(native, .horizontal, &glyph, &a, 1)
        CTFontGetAdvancesForGlyphs(converted, .horizontal, &glyph, &b, 1)
        maxAdvanceError = max(maxAdvanceError, abs(a.width - b.width))
        let ra = CTFontCreatePathForGlyph(native, glyph, nil)?.boundingBoxOfPath ?? .zero
        let rb = CTFontCreatePathForGlyph(converted, glyph, nil)?.boundingBoxOfPath ?? .zero
        for delta in [ra.minX-rb.minX, ra.minY-rb.minY, ra.maxX-rb.maxX, ra.maxY-rb.maxY] {
            maxBoundsError = max(maxBoundsError, abs(delta))
        }
    }
    precondition(maxAdvanceError <= 0.032, "Advance mismatch: \(name) \(maxAdvanceError)")
    precondition(maxBoundsError <= 0.04, "Outline mismatch: \(name) \(maxBoundsError)")
    faces.append(["name": name, "version": version, "glyphs": count,
                  "maxAdvanceErrorAt16px": maxAdvanceError * 0.016,
                  "maxBoundsErrorAt16px": maxBoundsError * 0.016])
    for size: CGFloat in [12, 16, 24, 32, 64] {
        for text in ["ABC 0123", "mmmmmmmmmmlli", "The quick brown fox 0123456789",
                     "中文测试，香港臺灣澳門。", "你好世界 () % & ! *", "骨直令雨返遍邊言說體鬱龜龍"] {
            var images: [Data] = [], widths: [Double] = [], glyphs: [[Int]] = []
            var nativeAdvances: [Double] = []
            for font in [CTFontCreateWithName(name as CFString, size, nil),
                         CTFontCreateWithFontDescriptor(descriptor, size, nil)] {
                let context = CGContext(data: nil, width: 2000, height: 120, bitsPerComponent: 8,
                    bytesPerRow: 2000, space: CGColorSpaceCreateDeviceGray(),
                    bitmapInfo: CGImageAlphaInfo.none.rawValue)!
                context.setFillColor(CGColor(gray: 1, alpha: 1))
                context.fill(CGRect(x: 0, y: 0, width: 2000, height: 120))
                context.setShouldSmoothFonts(false)
                context.setShouldSubpixelPositionFonts(true)
                context.setShouldSubpixelQuantizeFonts(false)
                context.textPosition = CGPoint(x: 10, y: 35)
                let line = CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: [
                    NSAttributedString.Key(kCTFontAttributeName as String): font,
                    NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(gray: 0, alpha: 1)]))
                widths.append(CTLineGetTypographicBounds(line, nil, nil, nil))
                var ids: [Int] = [], advances: [Double] = []
                for run in CTLineGetGlyphRuns(line) as! [CTRun] {
                    let runFont = (CTRunGetAttributes(run) as NSDictionary)[kCTFontAttributeName] as! CTFont
                    precondition(CTFontCopyPostScriptName(runFont) as String == name, "Unexpected fallback")
                    let count = CTRunGetGlyphCount(run)
                    var g = [CGGlyph](repeating: 0, count: count), a = [CGSize](repeating: .zero, count: count)
                    CTRunGetGlyphs(run, CFRange(), &g)
                    CTRunGetAdvances(run, CFRange(), &a)
                    ids += g.map { Int($0) }; advances += a.map { $0.width }
                }
                glyphs.append(ids)
                if images.isEmpty { nativeAdvances = advances }
                CTLineDraw(line, context)
                images.append(Data(bytes: context.data!, count: 2000 * 120))
            }
            precondition(glyphs[0] == glyphs[1], "Shaping mismatch: \(name) \(text)")
            let widthDelta = widths[1] - widths[0]
            precondition(abs(widthDelta) <= Double(glyphs[0].count) * size / 32000 + 0.00001)
            var changed = 0, maximum = 0, total = 0
            for (a, b) in zip(images[0], images[1]) {
                let delta = abs(Int(a) - Int(b))
                if delta > 0 { changed += 1 }
                maximum = max(maximum, delta); total += delta
            }
            samples.append(["name": name, "size": size, "text": text, "glyphs": glyphs[0],
                            "nativeAdvances": nativeAdvances, "nativeWidth": widths[0], "widthDelta": widthDelta,
                            "changedPixels": changed, "maxPixelDelta": maximum, "absolutePixelDelta": total])
        }
    }
}
let report: [String: Any] = ["faces": faces, "samples": samples]
print(String(data: try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]), encoding: .utf8)!)
