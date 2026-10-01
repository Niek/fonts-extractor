import Foundation
import CoreText
import CoreGraphics
import CryptoKit
let outDir=URL(fileURLWithPath:CommandLine.arguments[1],isDirectory:true)
try FileManager.default.createDirectory(at:outDir,withIntermediateDirectories:true)
let weights=["Ultralight","Thin","Light","Regular","Medium","Semibold"]
let texts=["ABC 0123", "The quick brown fox jumps over the lazy dog. 0123456789", "mmmmmmmmmmlli", "中文测试，香港臺灣澳門。", "你好世界 () % & ! *", "骨直令雨返遍邊言說體鬱龜龍", "简体中文与繁體中文：汉字字体排版", "，。！？：；、（）【】《》“”‘’…—", "Hello 世界 123.45 € $ ¥ @ # % &", "á é ü ñ Å ﬁ ﬂ", "𠮷𠀀𰀀𱍐"]
func font(_ name:String,_ size:CGFloat)->CTFont {
 let f=CTFontCreateWithName(name as CFString,size,nil)
 precondition(CTFontCopyPostScriptName(f) as String == name)
 return f
}
func commands(_ path:CGPath?) -> [Double] {
 var values:[Double]=[]
 path?.applyWithBlock { ptr in
  let e=ptr.pointee
  values.append(Double(e.type.rawValue))
  let count:Int
  switch e.type {case .moveToPoint,.addLineToPoint:count=1;case .addQuadCurveToPoint:count=2;case .addCurveToPoint:count=3;case .closeSubpath:count=0;@unknown default:fatalError()}
  for i in 0..<count { values.append(e.points[i].x); values.append(e.points[i].y) }
 }
 return values
}
func bytes(_ values:[Double])->Data {values.withUnsafeBytes{Data($0)}}
var metadata:[[String:Any]]=[]
var samples:[[String:Any]]=[]
for family in ["SC","TC","HK","MO"] {
 for weight in weights {
  let name="PingFang\(family)-\(weight)"
  let base=font(name,1000)
  let f=CTFontCreateCopyWithAttributes(base,1000,nil,CTFontDescriptorCreateWithAttributes([kCTFontOpticalSizeAttribute:"none"] as CFDictionary))
  let count=CTFontGetGlyphCount(f)
  let path=(CTFontCopyAttribute(f,kCTFontURLAttribute) as! URL).path
  metadata.append(["name":name,"version":CTFontCopyName(f,kCTFontVersionNameKey)! as String,"source":path,"glyphCount":count,"upem":CTFontGetUnitsPerEm(f),"ascent":CTFontGetAscent(f),"descent":CTFontGetDescent(f),"leading":CTFontGetLeading(f)])
  var records=Data(); records.reserveCapacity(count*88)
  for i in 0..<count {
   autoreleasepool {
    var g=CGGlyph(i); var h=CGSize.zero,v=CGSize.zero
    CTFontGetAdvancesForGlyphs(f,.horizontal,&g,&h,1)
    CTFontGetAdvancesForGlyphs(f,.vertical,&g,&v,1)
    let path=CTFontCreatePathForGlyph(f,g,nil)
    let c=commands(path)
    let b=path?.boundingBoxOfPath ?? .zero
    records.append(bytes([h.width,v.height,b.origin.x,b.origin.y,b.width,b.height,Double(c.count)]))
    records.append(contentsOf:SHA256.hash(data:bytes(c.map{($0*1e6).rounded()/1e6})))
   }
  }
  try records.write(to:outDir.appendingPathComponent(name+".bin"))
  for size:CGFloat in [12,16,32] {
   let f=font(name,size)
   for text in texts {
    let line=CTLineCreateWithAttributedString(NSAttributedString(string:text,attributes:[NSAttributedString.Key(kCTFontAttributeName as String):f]))
    var runs:[[String:Any]]=[]
    for run in CTLineGetGlyphRuns(line) as! [CTRun] {
     let attrs=CTRunGetAttributes(run) as NSDictionary
     let rf=attrs[kCTFontAttributeName] as! CTFont
     let n=CTRunGetGlyphCount(run)
     var glyphs=[CGGlyph](repeating:0,count:n),pos=[CGPoint](repeating:.zero,count:n),adv=[CGSize](repeating:.zero,count:n)
     CTRunGetGlyphs(run,CFRange(),&glyphs);CTRunGetPositions(run,CFRange(),&pos);CTRunGetAdvances(run,CFRange(),&adv)
     runs.append(["font":CTFontCopyPostScriptName(rf) as String,"version":CTFontCopyName(rf,kCTFontVersionNameKey) as String? ?? "","glyphs":glyphs.map{Int($0)},"positions":pos.map{[$0.x,$0.y]},"advances":adv.map{[$0.width,$0.height]},"paths":glyphs.map{commands(CTFontCreatePathForGlyph(rf,$0,nil))}])
    }
    samples.append(["name":name,"size":size,"text":text,"width":CTLineGetTypographicBounds(line,nil,nil,nil),"runs":runs])
   }
  }
  print(name,count);fflush(stdout)
 }
}
try JSONSerialization.data(withJSONObject:metadata,options:[.sortedKeys,.prettyPrinted]).write(to:outDir.appendingPathComponent("metadata.json"))
try JSONSerialization.data(withJSONObject:samples,options:[.sortedKeys]).write(to:outDir.appendingPathComponent("samples.json"))
