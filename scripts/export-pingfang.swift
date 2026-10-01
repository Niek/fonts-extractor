import Foundation
import CoreText
import CoreGraphics

// CoreText decodes Apple's hvgl outlines; export the public named weight.
let name = CommandLine.arguments[1]
let native = CTFontCreateWithName(name as CFString, 1000, nil)
precondition(CTFontCopyPostScriptName(native) as String == name, "Font substitution")
let font = CTFontCreateCopyWithAttributes(native, 1000, nil,
    CTFontDescriptorCreateWithAttributes([kCTFontOpticalSizeAttribute: "none"] as CFDictionary))
let source = (CTFontCopyAttribute(font, kCTFontURLAttribute) as! URL).path
precondition(source.hasSuffix("/Reserved/PingFangUI.ttc"))
func emit(_ value: [String: Any]) {
    FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: value))
    FileHandle.standardOutput.write(Data([10]))
}
emit(["name": name, "source": source, "version": CTFontCopyName(font, kCTFontVersionNameKey)! as String,
      "glyphs": CTFontGetGlyphCount(font), "upem": CTFontGetUnitsPerEm(font)])
for i in 0..<CTFontGetGlyphCount(font) {
    autoreleasepool {
        var glyph = CGGlyph(i)
        var horizontal = CGSize.zero, vertical = CGSize.zero, origin = CGSize.zero
        CTFontGetAdvancesForGlyphs(font, .horizontal, &glyph, &horizontal, 1)
        let verticalAdvance = CTFontGetAdvancesForGlyphs(font, .vertical, &glyph, &vertical, 1)
        CTFontGetVerticalTranslationsForGlyphs(font, &glyph, &origin, 1)
        var commands: [[Double]] = []
        CTFontCreatePathForGlyph(font, glyph, nil)?.applyWithBlock { pointer in
            let element = pointer.pointee
            var command = [Double(element.type.rawValue)]
            let count: Int
            switch element.type {
            case .moveToPoint, .addLineToPoint: count = 1
            case .addQuadCurveToPoint: count = 2
            case .closeSubpath: count = 0
            default: fatalError("Expected quadratic PingFang outlines")
            }
            for j in 0..<count { command += [element.points[j].x, element.points[j].y] }
            commands.append(command)
        }
        emit(["gid": i, "advance": horizontal.width, "verticalAdvance": verticalAdvance,
              "verticalOrigin": [origin.width, origin.height], "path": commands])
    }
}
