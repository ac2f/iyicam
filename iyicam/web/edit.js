/* iyiCAM page: editing the drawing (select, move, draw, nodes, commands, undo), layers and sheets, nesting.
 *
 * It sits on app.js (through ``window.IyiPage``) and geom.js (``window.IyiGeom``).  Simple things are done here, in the
 * browser, on the JSON of the project (move, turn, flip, copy, delete, the drawing tools, the nodes of a vector); all
 * the rest is the engine's own vector commands (``command``: offset, weld, arrays, fillets, ...) and nesting (``nest``),
 * called with the project and answered with the changed project.  Every change can be undone. */
(function () {
  "use strict";
  const B = window.IyiPage, G = window.IyiGeom;
  if (!B || !G) return;
  const { S, h, $, t, api, runJob, toast, fail, openSheet, paramsForm, numberInput, fmt, TXT } = B;

  // ------------------------------------------------------------------------------------------ texts
  Object.assign(TXT.tr, {
    "ed.view": "Gez: sürükle kaydır, dokun seç", "ed.select": "Kutuyla seç (soldan sağa: içine tam girenler, sağdan sola: değenler)",
    "ed.move": "Taşı: seçili vektörü sürükle", "ed.line": "Çizgi", "ed.poly": "Çoklu çizgi (noktalara dokun)", "ed.rect": "Dikdörtgen",
    "ed.circle": "Daire (merkezden sürükle)", "ed.ellipse": "Elips", "ed.polygon": "Çokgen (merkezden sürükle)",
    "ed.nodes": "Düğümler", "ed.start": "Başlangıç noktası (vektörün üstüne dokun)",
    "ed.undo": "Geri al", "ed.redo": "Yinele", "ed.multi": "Çoklu seçim", "ed.all": "Hepsini seç", "ed.invert": "Seçimi ters çevir",
    "ed.none": "Seçimi kaldır", "ed.snap": "Yakalama", "ed.grid": "Izgara", "ed.cmds": "Komutlar", "ed.sides": "Kenar",
    "ed.transform": "Dönüştür: konum, boyut, açı ve ölçeği rakam yazarak ver", "ed.help": "Düğmeler ne işe yarar?", "ed.labels": "Düğme altındaki yazıları aç / kapat",
    "ed.s.view": "Gez", "ed.s.select": "Kutu", "ed.s.move": "Taşı", "ed.s.line": "Çizgi", "ed.s.poly": "Çoklu", "ed.s.rect": "Dikd.", "ed.s.circle": "Daire",
    "ed.s.ellipse": "Elips", "ed.s.polygon": "Çokgen", "ed.s.nodes": "Düğüm", "ed.s.start": "Başlat", "ed.s.undo": "Geri", "ed.s.redo": "İleri",
    "ed.s.multi": "Çoklu", "ed.s.all": "Tümü", "ed.s.invert": "Ters", "ed.s.none": "Hiç", "ed.s.transform": "Dönüş.", "ed.s.snap": "Yakala",
    "ed.s.grid": "Izgara", "ed.s.cmds": "Komut", "ed.s.help": "Yardım", "ed.s.rot": "90°", "ed.s.flipH": "Sağ-sol", "ed.s.flipV": "Alt-üst",
    "ed.s.dup": "Çoğalt", "ed.s.copy": "Kopya", "ed.s.paste": "Yapıştır", "ed.s.delete": "Sil", "ed.s.done": "Bitti", "ed.s.last": "Son sil",
    "ed.s.cancel": "İptal", "ed.s.ni": "Ekle", "ed.s.nd": "Sil", "ed.s.ns": "Başlat", "ed.s.nb": "Ayır", "ed.s.nk": "Kapat", "ed.s.nl": "Çizgi", "ed.s.nc": "Eğri",
    "tf.title": "Dönüştür", "tf.none": "Önce vektör seç.", "tf.sel": "{0} vektör · sol-alt ({1}, {2}) · boyut {3} × {4}", "tf.ref": "Referans noktası",
    "tf.pos": "Konum (referans noktasının koordinatı)", "tf.go": "Bu konuma taşı", "tf.by": "Kaydır", "tf.move": "Kaydır", "tf.size": "Boyut", "tf.w": "Genişlik",
    "tf.h": "Yükseklik", "tf.prop": "Oranı koru", "tf.sizeGo": "Boyutlandır", "tf.scale": "Ölçek (%)", "tf.sx": "Yatay %", "tf.sy": "Dikey %", "tf.scaleGo": "Ölçekle",
    "tf.rot": "Döndür", "tf.angle": "Açı (°)", "tf.about": "Merkez", "tf.about.ref": "Referans noktası", "tf.about.origin": "Sayfa başlangıcı (0, 0)",
    "tf.about.point": "Kendi noktam", "tf.rotGo": "Döndür", "tf.mirror": "Ayna", "tf.mh": "Sağ / sol", "tf.mv": "Alt / üst", "tf.slant": "Eğ",
    "tf.ax": "Yatay açı (°)", "tf.ay": "Dikey açı (°)", "tf.slantGo": "Eğ", "tf.copies": "Kopyalar",
    "tf.copiesN": "Kopya sayısı (0 = seçimin kendisi değişir, N = N kopya eklenir)", "tf.cant": "Bu dönüşüm yapılamadı.",
    "help.title": "Düğmeler ne işe yarar?", "help.tip": "Bir düğmeye uzun basarsan adı görünür. «Aa» düğmesi düğme altındaki yazıları açar ve kapatır.",
    "help.tools": "Araçlar (üst sıra)", "help.general": "Genel düğmeler", "help.selection": "Bir şey seçiliyken", "help.nodes": "Düğüm aracı",
    "help.poly": "Çoklu çizgi çizerken", "help.side": "Çizimin sağındaki düğmeler", "help.zoomin": "Yaklaştır", "help.zoomout": "Uzaklaştır",
    "files.search": "Dosya ara…", "files.sort.name": "Ad", "files.sort.date": "Tarih", "files.sort.size": "Boyut",
    "files.deep": "Alt klasörlerde de ara", "files.deepOff": "Aramayı kapat", "files.deepNone": "Eşleşen dosya yok.",
    "files.deepCut": "Arama çok uzadı, ilk bulunanlar gösteriliyor; daha özel yaz.", "files.more": "{0} satır daha var: aramayı daralt.",
    "files.cut": "Klasör çok büyük; sıralamaya göre ilk 3000 dosya gösteriliyor.",
    "ed.order": "Kesim sırası numaraları", "ed.selected": "{0} seçili", "ed.nothing": "Hiçbir şey seçili değil.", "ed.minsel": "Bu komut için en az {0} vektör seç.",
    "ed.delete": "Sil", "ed.dup": "Çoğalt", "ed.copy": "Kopyala", "ed.paste": "Yapıştır", "ed.flipH": "Sağ/sol çevir", "ed.flipV": "Alt/üst çevir",
    "ed.rot": "90° döndür", "ed.layer": "Katmana taşı", "ed.more": "Daha fazlası",
    "ed.done": "Bitti", "ed.close": "Kapat", "ed.cancel": "Vazgeç", "ed.apply": "Uygula", "ed.search": "Komut ara…", "ed.last": "Sonuncuyu sil",
    "ed.undone": "Geri alındı", "ed.redone": "Yinelendi", "ed.cmdsTitle": "Vektör komutları", "ed.cmdDone": "{0} uygulandı",
    "ed.nochange": "Bir şey değişmedi.", "ed.pasted": "{0} vektör yapıştırıldı", "ed.deleted": "{0} vektör silindi",
    "ed.node.insert": "Düğüm ekle (çizgiye dokun)", "ed.node.del": "Düğümü sil", "ed.node.start": "Başlangıç buradan", "ed.node.break": "Düğümden ayır",
    "ed.node.close": "Kapat", "ed.node.line": "Çizgi yap", "ed.node.curve": "Eğri yap", "ed.node.pick": "Düğümleri görmek için tek bir vektör seç.",
    "ed.node.cant": "Bu düğüm silinemez.", "ed.startSet": "Başlangıç noktası ayarlandı.",
    "lay.add": "Katman ekle", "lay.active": "Çizim katmanı", "lay.lock": "Kilitle", "lay.del": "Katmanı sil", "lay.name": "Katman",
    "lay.notEmpty": "Katmanda vektör var; önce onları başka katmana taşı.", "lay.last": "Son katman silinemez.",
    "sheet.add": "Sayfa ekle", "sheet.del": "Sayfayı sil", "sheet.last": "Son sayfa silinemez.", "sheet.delask": "Bu sayfa ve vektörleri silinsin mi?",
    "proj.new": "＋ Boş proje", "proj.newname": "Yeni proje", "proj.sheetthk": "Kalınlık (bu sayfa)",
    "proj.home": "Ev konumu (makine sonunda gider)", "proj.rapid": "Hızlı hareket (mm/dk)", "proj.rotary": "Döner eksen (A)", "proj.rotdia": "Döner eksen çapı (mm)",
    "nest.title": "Yerleştirme (nesting)", "nest.plate": "Plaka", "nest.w": "Genişlik (mm)", "nest.h": "Yükseklik (mm)", "nest.margin": "Kenar boşluğu (mm)",
    "nest.spacing": "Parça aralığı (mm)", "nest.rot": "Dönüş adımı (°, 0 = dönme)", "nest.iter": "Arama (deneme sayısı)", "nest.sel": "Yalnız seçilileri yerleştir",
    "nest.copies": "Her parçadan kaç tane", "nest.run": "Yerleştir", "nest.result": "{0} parça yerleşti, {1} sığmadı, {2} plaka.",
    "nest.nothing": "Yerleştirilecek vektör yok.", "nest.squeeze": "Sıkıştırma önerisi (°, 0 = yok)",
    "nest.how": "Dizme", "nest.arrange": "Parçalar nasıl dizilsin?", "nest.a.free": "Serbest (iç içe geçer, en az malzeme)",
    "nest.a.rows": "X ekseni boyunca: satır satır", "nest.a.columns": "Y ekseni boyunca: sütun sütun",
    "nest.corner": "Başlangıç köşesi", "nest.c.bl": "Sol alt", "nest.c.br": "Sağ alt", "nest.c.tl": "Sol üst", "nest.c.tr": "Sağ üst",
    "nest.gravity": "Serbest dizmede yön", "nest.g.auto": "Otomatik (en iyisini dene)", "nest.g.left": "Sola yasla (Y boyunca dolar)",
    "nest.g.bottom": "Aşağı yasla (X boyunca dolar)", "nest.g.compact": "En küçük alan", "nest.g.reach_x": "X'te en kısa", "nest.g.reach_y": "Y'de en kısa",
    "nest.g.diag": "Sol alt köşeye (çapraz)",
    "e.command_failed": "Komut uygulanamadı.", "e.nothing_to_nest": "Yerleştirilecek vektör yok.",
    "paths.bylayers": "Katmanlar", "paths.byvectors": "Seçili vektörler ({0})", "paths.novec": "Önce çizimde vektör seç.",
    "paths.order": "Kesim sırası ve başlangıç noktaları", "paths.up": "Yukarı", "paths.down": "Aşağı", "paths.dup": "Çoğalt",
    "seq.auto": "Sırayı otomatik düzenle", "seq.strategy": "Sıra stratejisi", "seq.grouping": "Gruplama", "seq.direction": "Kesim yönü",
    "seq.band": "Şerit genişliği (mm, 0 = otomatik)", "seq.smart": "Akıllı başlangıç noktaları", "seq.margin": "Başlangıç payı (mm)",
    "seq.corner": "Köşeden uzaklık (mm)",
    "ed.shift": "Shift tuşu (taklit): seçime ekle / çıkar; çizerken merkezden çizer; kaydırma düğmeleri 10 kat",
    "ed.ctrl": "Ctrl tuşu (taklit): seçime ekle / çıkar; taşırken yatay-dikey kilitler; çizerken düz tutar (kare, daire, 15° adım)",
    "ed.alt": "Alt tuşu (taklit): dokununca altındaki vektörü seç; kutuyla değenleri seç; o hareket için yakalamayı kapatır",
    "ed.s.shift": "Shift", "ed.s.ctrl": "Ctrl", "ed.s.alt": "Alt", "ed.mod.once": "{0}: bir sonraki işlem için basılı", "ed.mod.lock": "{0}: kilitli, kapatmak için bir daha dokun",
    "ed.mod.off": "{0}: kapalı", "ed.s.fold": "Daralt", "ed.fold": "Düğme satırlarını daralt / aç", "ed.s.ref": "Nokta",
    "ed.goto": "Seçimi yazdığın koordinata taşı", "ed.s.goto": "Taşı", "ed.coord": "Koordinat satırını aç / kapat", "ed.s.coord": "Konum",
    "ed.ref": "Referans noktası: yazdığın koordinat seçimin bu noktasına gelir", "ed.step": "Kaydırma adımı (mm)", "ed.nudge": "Seçimi {0} mm kaydır (Shift ile 10 kat)",
    "ref.tl": "↖ Sol-üst", "ref.tc": "↑ Üst orta", "ref.tr": "↗ Sağ-üst", "ref.ml": "← Sol orta", "ref.mc": "● Merkez", "ref.mr": "→ Sağ orta",
    "ref.bl": "↙ Sol-alt", "ref.bc": "↓ Alt orta", "ref.br": "↘ Sağ-alt", "ed.moved": "Seçim ({0}, {1}) konumuna taşındı",
    "ed.layers": "Katmanlar: sırala, tümünü seç, gizle, kilitle", "ed.s.layers": "Katman",
    "lay.title": "Katmanlar", "lay.select": "Bu katmandaki tüm vektörleri seç", "lay.up": "Katmanı yukarı al", "lay.down": "Katmanı aşağı al",
    "lay.selected": "{0} vektör seçildi ({1})", "lay.empty": "Bu sayfada bu katmanda vektör yok.", "lay.hidden": "Katman gizli ya da kilitli; önce göster / kilidini aç.",
    "lay.vis": "Göster / gizle", "lay.hint": "◉ çizim katmanı: yeni vektörler buna çizilir. ↑ ↓ sırayı değiştirir, ◎ katmandaki tüm vektörleri seçer.",
    "help.mods": "Shift / Ctrl / Alt (telefonda tuş yok: bir kez dokun = sonraki işlem için, iki kez = kilitli, üç kez = kapalı)", "help.coord": "Koordinat satırı (bir şey seçiliyken)",
  });
  Object.assign(TXT.en, {
    "ed.view": "View: drag to pan, tap to select", "ed.select": "Select with a box (left to right: only what is inside, right to left: what it touches)",
    "ed.move": "Move: drag the selected vectors", "ed.line": "Line", "ed.poly": "Polyline (tap the points)", "ed.rect": "Rectangle",
    "ed.circle": "Circle (drag from the centre)", "ed.ellipse": "Ellipse", "ed.polygon": "Polygon (drag from the centre)",
    "ed.nodes": "Nodes", "ed.start": "Start point (tap on a vector)",
    "ed.undo": "Undo", "ed.redo": "Redo", "ed.multi": "Select several", "ed.all": "Select all", "ed.invert": "Invert selection",
    "ed.none": "Select nothing", "ed.snap": "Snapping", "ed.grid": "Grid", "ed.cmds": "Commands", "ed.sides": "Sides",
    "ed.transform": "Transform: give position, size, angle and scale as numbers", "ed.help": "What is every button?", "ed.labels": "Show / hide the names under the buttons",
    "ed.s.view": "View", "ed.s.select": "Box", "ed.s.move": "Move", "ed.s.line": "Line", "ed.s.poly": "Poly", "ed.s.rect": "Rect", "ed.s.circle": "Circle",
    "ed.s.ellipse": "Ellipse", "ed.s.polygon": "Polygon", "ed.s.nodes": "Nodes", "ed.s.start": "Start", "ed.s.undo": "Undo", "ed.s.redo": "Redo",
    "ed.s.multi": "Multi", "ed.s.all": "All", "ed.s.invert": "Invert", "ed.s.none": "None", "ed.s.transform": "Transf.", "ed.s.snap": "Snap",
    "ed.s.grid": "Grid", "ed.s.cmds": "Cmds", "ed.s.help": "Help", "ed.s.rot": "90°", "ed.s.flipH": "Flip H", "ed.s.flipV": "Flip V",
    "ed.s.dup": "Dupl.", "ed.s.copy": "Copy", "ed.s.paste": "Paste", "ed.s.delete": "Delete", "ed.s.done": "Done", "ed.s.last": "Undo pt",
    "ed.s.cancel": "Cancel", "ed.s.ni": "Insert", "ed.s.nd": "Delete", "ed.s.ns": "Start", "ed.s.nb": "Break", "ed.s.nk": "Close", "ed.s.nl": "Line", "ed.s.nc": "Curve",
    "tf.title": "Transform", "tf.none": "Select vectors first.", "tf.sel": "{0} vector(s) · lower left ({1}, {2}) · size {3} × {4}", "tf.ref": "Reference point",
    "tf.pos": "Position (the coordinates of the reference point)", "tf.go": "Move to this position", "tf.by": "Move by", "tf.move": "Move", "tf.size": "Size", "tf.w": "Width",
    "tf.h": "Height", "tf.prop": "Keep the proportions", "tf.sizeGo": "Set the size", "tf.scale": "Scale (%)", "tf.sx": "Horizontal %", "tf.sy": "Vertical %", "tf.scaleGo": "Scale",
    "tf.rot": "Rotate", "tf.angle": "Angle (°)", "tf.about": "Centre", "tf.about.ref": "Reference point", "tf.about.origin": "Sheet origin (0, 0)",
    "tf.about.point": "A point of my own", "tf.rotGo": "Rotate", "tf.mirror": "Mirror", "tf.mh": "Left / right", "tf.mv": "Top / bottom", "tf.slant": "Slant",
    "tf.ax": "Horizontal angle (°)", "tf.ay": "Vertical angle (°)", "tf.slantGo": "Slant", "tf.copies": "Copies",
    "tf.copiesN": "Number of copies (0 = the selection itself changes, N = N copies are added)", "tf.cant": "This could not be done.",
    "help.title": "What is every button?", "help.tip": "A long press on a button tells its name. The «Aa» button shows or hides the names under the buttons.",
    "help.tools": "Tools (top row)", "help.general": "General buttons", "help.selection": "With something selected", "help.nodes": "The node tool",
    "help.poly": "While drawing a polyline", "help.side": "Buttons at the right of the drawing", "help.zoomin": "Zoom in", "help.zoomout": "Zoom out",
    "files.search": "Search files…", "files.sort.name": "Name", "files.sort.date": "Date", "files.sort.size": "Size",
    "files.deep": "Search the sub-folders too", "files.deepOff": "Close the search", "files.deepNone": "No file matches.",
    "files.deepCut": "The search took too long; the first finds are shown. Be more specific.", "files.more": "{0} more: narrow the search.",
    "files.cut": "The folder is too big: the first 3000 files in this order are shown.",
    "ed.order": "Show the cut order", "ed.selected": "{0} selected", "ed.nothing": "Nothing is selected.", "ed.minsel": "This command needs at least {0} selected vector(s).",
    "ed.delete": "Delete", "ed.dup": "Duplicate", "ed.copy": "Copy", "ed.paste": "Paste", "ed.flipH": "Flip left / right", "ed.flipV": "Flip top / bottom",
    "ed.rot": "Turn 90°", "ed.layer": "Move to layer", "ed.more": "More",
    "ed.done": "Done", "ed.close": "Close", "ed.cancel": "Cancel", "ed.apply": "Apply", "ed.search": "Search commands…", "ed.last": "Remove the last",
    "ed.undone": "Undone", "ed.redone": "Redone", "ed.cmdsTitle": "Vector commands", "ed.cmdDone": "{0} done",
    "ed.nochange": "Nothing changed.", "ed.pasted": "{0} vector(s) pasted", "ed.deleted": "{0} vector(s) deleted",
    "ed.node.insert": "Insert a node (tap on the line)", "ed.node.del": "Delete the node", "ed.node.start": "Start here", "ed.node.break": "Break at the node",
    "ed.node.close": "Close", "ed.node.line": "Make a line", "ed.node.curve": "Make a curve", "ed.node.pick": "Select one vector to see its nodes.",
    "ed.node.cant": "This node cannot be deleted.", "ed.startSet": "Start point set.",
    "lay.add": "Add a layer", "lay.active": "Drawing layer", "lay.lock": "Lock", "lay.del": "Delete the layer", "lay.name": "Layer",
    "lay.notEmpty": "The layer has vectors; move them to another layer first.", "lay.last": "The last layer cannot be deleted.",
    "sheet.add": "Add a sheet", "sheet.del": "Delete the sheet", "sheet.last": "The last sheet cannot be deleted.", "sheet.delask": "Delete this sheet and its vectors?",
    "proj.new": "＋ Empty project", "proj.newname": "New project", "proj.sheetthk": "Thickness (this sheet)",
    "proj.home": "Home position (where the machine ends)", "proj.rapid": "Rapid rate (mm/min)", "proj.rotary": "Rotary axis (A)", "proj.rotdia": "Rotary diameter (mm)",
    "nest.title": "Nesting", "nest.plate": "Plate", "nest.w": "Width (mm)", "nest.h": "Height (mm)", "nest.margin": "Border gap (mm)",
    "nest.spacing": "Part spacing (mm)", "nest.rot": "Rotation step (°, 0 = no turning)", "nest.iter": "Search (attempts)", "nest.sel": "Nest only the selected",
    "nest.copies": "Copies of each part", "nest.run": "Nest", "nest.result": "{0} part(s) placed, {1} did not fit, {2} plate(s).",
    "nest.nothing": "There is nothing to nest.", "nest.squeeze": "Squeeze suggestion (°, 0 = none)",
    "nest.how": "Arrangement", "nest.arrange": "How are the parts put down?", "nest.a.free": "Free (they interlock, least material)",
    "nest.a.rows": "Along the X axis: row by row", "nest.a.columns": "Along the Y axis: column by column",
    "nest.corner": "Start in the corner", "nest.c.bl": "Bottom left", "nest.c.br": "Bottom right", "nest.c.tl": "Top left", "nest.c.tr": "Top right",
    "nest.gravity": "Direction of free nesting", "nest.g.auto": "Automatic (try the best)", "nest.g.left": "Pack to the left (fills along Y)",
    "nest.g.bottom": "Pack to the bottom (fills along X)", "nest.g.compact": "Smallest area", "nest.g.reach_x": "Shortest along X", "nest.g.reach_y": "Shortest along Y",
    "nest.g.diag": "To the lower left corner (diagonal)",
    "e.command_failed": "The command could not be applied.", "e.nothing_to_nest": "There is nothing to nest.",
    "paths.bylayers": "Layers", "paths.byvectors": "Selected vectors ({0})", "paths.novec": "Select vectors on the drawing first.",
    "paths.order": "Cut order and start points", "paths.up": "Up", "paths.down": "Down", "paths.dup": "Duplicate",
    "seq.auto": "Order the cuts automatically", "seq.strategy": "Order strategy", "seq.grouping": "Grouping", "seq.direction": "Cutting direction",
    "seq.band": "Band width (mm, 0 = automatic)", "seq.smart": "Smart start points", "seq.margin": "Start margin (mm)",
    "seq.corner": "Distance from a corner (mm)",
    "ed.shift": "Shift key (emulated): add to / remove from the selection; draws from the centre; the nudge buttons go 10 times as far",
    "ed.ctrl": "Ctrl key (emulated): add to / remove from the selection; keeps a move horizontal or vertical; keeps a shape regular (square, circle, 15° steps)",
    "ed.alt": "Alt key (emulated): a tap picks the vector behind; a box takes what it touches; switches snapping off for that gesture",
    "ed.s.shift": "Shift", "ed.s.ctrl": "Ctrl", "ed.s.alt": "Alt", "ed.mod.once": "{0}: held for the next action", "ed.mod.lock": "{0}: locked, tap again to release",
    "ed.mod.off": "{0}: off", "ed.s.fold": "Fold", "ed.fold": "Fold / unfold the rows of buttons", "ed.s.ref": "Point",
    "ed.goto": "Move the selection to the coordinates you typed", "ed.s.goto": "Move", "ed.coord": "Show / hide the coordinate row", "ed.s.coord": "Position",
    "ed.ref": "Reference point: the coordinates you type are those of this point of the selection", "ed.step": "Nudge step (mm)", "ed.nudge": "Nudge the selection by {0} mm (10 times with Shift)",
    "ref.tl": "↖ Top-left", "ref.tc": "↑ Top middle", "ref.tr": "↗ Top-right", "ref.ml": "← Left middle", "ref.mc": "● Centre", "ref.mr": "→ Right middle",
    "ref.bl": "↙ Bottom-left", "ref.bc": "↓ Bottom middle", "ref.br": "↘ Bottom-right", "ed.moved": "The selection was moved to ({0}, {1})",
    "ed.layers": "Layers: reorder, select all of one, show, lock", "ed.s.layers": "Layers",
    "lay.title": "Layers", "lay.select": "Select all the vectors of this layer", "lay.up": "Move the layer up", "lay.down": "Move the layer down",
    "lay.selected": "{0} vector(s) selected ({1})", "lay.empty": "This layer has no vectors on this sheet.", "lay.hidden": "The layer is hidden or locked; show it / unlock it first.",
    "lay.vis": "Show / hide", "lay.hint": "◉ the drawing layer: new vectors go on it. ↑ ↓ change the order, ◎ selects every vector of the layer.",
    "help.mods": "Shift / Ctrl / Alt (a phone has no keys: tap once = for the next action, twice = locked, three times = off)", "help.coord": "The coordinate row (with something selected)",
  });

  // ------------------------------------------------------------------------------------------ state
  const E = {
    tool: "view", sel: new Set(), multi: false, snap: true, grid: 0, sides: 6, layer: null, sheetId: null, order: false,
    labels: B.store.get("iyicam.labels") !== "0", tf: null,
    undo: [], redo: [], clip: null, pastes: 0, node: null, insert: false, poly: [], ghost: null, mark: null, rubber: null,
    mod: { shift: 0, ctrl: 0, alt: 0 },            // the keys a phone does not have: 0 off, 1 held for the next action, 2 locked
    coord: B.store.get("iyicam.coord") !== "0", ref: B.store.get("iyicam.ref") || "bl", step: +B.store.get("iyicam.step") || 1,
    fold: B.store.get("iyicam.fold") === null ? innerHeight < 520 : B.store.get("iyicam.fold") === "1",       // (a phone on its side starts with the rows folded)
  };
  // ---- Shift, Ctrl and Alt: the real keys (a mouse, a keyboard) and the buttons that stand for them (a phone)
  const MODS = ["shift", "ctrl", "alt"];
  /** which of the three keys count for this event: the key is down, or its button is on */
  function mods(e) {
    return { shift: E.mod.shift > 0 || !!(e && e.shiftKey), ctrl: E.mod.ctrl > 0 || !!(e && (e.ctrlKey || e.metaKey)), alt: E.mod.alt > 0 || !!(e && e.altKey) };
  }
  /** a button that was held for one action lets go of it (a locked one stays); true when something changed */
  function releaseMods() {
    let any = false;
    for (const k of MODS) if (E.mod[k] === 1) { E.mod[k] = 0; any = true; }
    return any;
  }
  const sheet = () => B.activeSheet();
  const layerOf = v => B.layerMap(S.project).get(v.layer);
  const usable = v => { const l = layerOf(v); return !l || (l.visible && !l.locked); };      // (a hidden or locked layer is not touched)
  const selected = () => sheet().vectors.filter(v => E.sel.has(v.id));
  const activeLayer = () => {
    const ls = S.project.layers;
    return ls.find(l => l.id === E.layer && !l.locked) || ls.find(l => !l.locked) || ls[0];
  };
  const keepSelection = () => {                       // (ids that are not on this sheet any more are dropped)
    const ids = new Set(sheet().vectors.map(v => v.id));
    for (const id of [...E.sel]) if (!ids.has(id)) E.sel.delete(id);
  };
  const nodesOf = c => G.nodes(c);

  // ------------------------------------------------------------------------------------------ undo
  const MAX_UNDO = 40;
  /** What undo keeps: the project without its vectors (small) and, for each sheet, the list of its vector OBJECTS.  A
   *  vector is never changed, a changed one is a new object, so the objects can be kept as they are: a step costs a list of
   *  references, not a copy of a drawing of tens of megabytes. */
  const snapshot = () => ({ meta: JSON.stringify(S.project, (k, v) => (k === "vectors" ? undefined : v)),
                            vec: S.project.sheets.map(s => s.vectors.slice()), sel: [...E.sel], sheet: S.sheetId });
  /** how many steps are kept: 40, and 3 for a big drawing (a step that comes from the engine keeps the whole old drawing) */
  function trimUndo() {
    const cap = S.project && S.project.sheets.some(sh => B.sceneSegs(sh) > 30000) ? 3 : MAX_UNDO;
    while (E.undo.length > cap) E.undo.shift();
  }
  function remember() {
    E.undo.push(snapshot());
    E.redo = [];
    trimUndo();
  }
  function restore(s) {
    S.project = JSON.parse(s.meta);
    S.project.sheets.forEach((sh, i) => { sh.vectors = s.vec[i] || []; });
    S.sheetId = S.project.sheets.some(x => x.id === s.sheet) ? s.sheet : S.project.sheets[0].id;
    E.sel = new Set(s.sel); E.node = null; E.poly = [];
    keepSelection(); B.changed(); refreshLayers();
  }
  function undo() { const s = E.undo.pop(); if (!s) return; E.redo.push(snapshot()); restore(s); toast(t("ed.undone")); }
  function redo() { const s = E.redo.pop(); if (!s) return; E.undo.push(snapshot()); restore(s); toast(t("ed.redone")); }
  /** change the project: ``fn`` does it (on S.project); one step to undo */
  function edit(fn) { remember(); fn(S.project); B.changed(); }
  const setVectors = list => { const m = new Map(list.map(v => [v.id, v])); const sh = sheet(); sh.vectors = sh.vectors.map(v => m.get(v.id) || v); };

  // ------------------------------------------------------------------------------------------ what is done to a selection
  function mapSelected(map) {
    const out = [];
    for (const v of selected()) { const n = G.affine(v, map); if (!n) return null; out.push(n); }
    return out;
  }
  function selBox() { return G.bbox(selected(), B.flatOf); }
  function transformSelected(mapOf) {
    const bb = selBox(); if (!bb) return toast(t("ed.nothing"), "warn");
    const out = mapSelected(mapOf(bb));
    if (!out) return toast(t("e.command_failed"), "warn");
    edit(() => setVectors(out));
  }
  const rot90 = () => transformSelected(b => G.rotateMap(90, (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2));
  const flipH = () => transformSelected(b => G.flipHMap((b.x0 + b.x1) / 2));
  const flipV = () => transformSelected(b => G.flipVMap((b.y0 + b.y1) / 2));
  function moveSelected(dx, dy) {
    const out = selected().map(v => G.translate(v, dx, dy));
    if (out.length) edit(() => setVectors(out));
  }
  function removeSelected() {
    const ids = new Set(E.sel);
    if (!ids.size) return;
    edit(p => {
      sheet().vectors = sheet().vectors.filter(v => !ids.has(v.id));
      for (const tp of p.toolpaths) if (tp.selection && tp.selection.vectors) tp.selection.vectors = tp.selection.vectors.filter(i => !ids.has(i));
    });
    E.sel = new Set(); E.node = null;
    toast(t("ed.deleted", ids.size));
    B.render();
  }
  function duplicate() {
    const src = selected(); if (!src.length) return;
    const d = 10, news = src.map(v => ({ ...G.translate(v, d, d), id: G.newId() }));
    edit(() => { sheet().vectors.push(...news); });
    E.sel = new Set(news.map(v => v.id)); B.render();
  }
  function copy() {
    const src = selected(); if (!src.length) return;
    const lay = B.layerMap(S.project);
    E.clip = { vectors: JSON.parse(JSON.stringify(src)), names: src.map(v => (lay.get(v.layer) || {}).name) };
    E.pastes = 0;
    toast(t("ed.copy") + " ✓");
  }
  function paste() {
    if (!E.clip) return;
    E.pastes += 1;
    const d = 10 * E.pastes, lays = S.project.layers;
    const news = E.clip.vectors.map((v, i) => {
      const lay = lays.find(l => l.name === E.clip.names[i] && !l.locked) || activeLayer();
      return { ...G.translate(v, d, d), id: G.newId(), layer: lay.id };
    });
    edit(() => { sheet().vectors.push(...news); });
    E.sel = new Set(news.map(v => v.id)); B.render();
    toast(t("ed.pasted", news.length));
  }
  function toLayer(id) {
    const out = selected().map(v => ({ ...v, layer: id }));
    if (out.length) edit(() => setVectors(out));
  }
  const selectAll = () => { E.sel = new Set(sheet().vectors.filter(usable).map(v => v.id)); E.node = null; B.render(); };
  const invert = () => { E.sel = new Set(sheet().vectors.filter(v => usable(v) && !E.sel.has(v.id)).map(v => v.id)); E.node = null; B.render(); };
  const clear = () => { E.sel = new Set(); E.node = null; B.render(); };
  function addVector(contour, name) {
    const v = G.vectorOf([contour], activeLayer().id, name);
    edit(() => { sheet().vectors.push(v); });
    E.sel = new Set([v.id]); B.render();
  }

  // ------------------------------------------------------------------------------------------ nodes (one vector)
  const oneVector = () => { const l = selected(); return l.length === 1 ? l[0] : null; };
  function replaceContours(v, contours) { edit(() => setVectors([{ ...v, contours }])); }
  function nodeEdit(fn) {                         // fn(contour, k) -> contour | [contours] | null
    const v = oneVector(), n = E.node;
    if (!v || !n || !v.contours[n.ci]) return;
    const r = fn(v.contours[n.ci], n.k);
    if (!r) return toast(t("ed.node.cant"), "warn");
    const list = Array.isArray(r) ? r : [r], cs = v.contours.slice();
    cs.splice(n.ci, 1, ...list);
    replaceContours(v, cs);
    if (n.k >= G.nodes(list[0]).length) E.node = null;
  }
  /** where a one-finger gesture lands: a node or handle of the selected vector within ``tol`` (world units) */
  function nodeHit(x, y, tol) {
    const v = oneVector(); if (!v) return null;
    let best = null, bd = tol;
    const sel = E.node && v.contours[E.node.ci] ? E.node : null;
    if (sel) {                                     // (the handles of the selected node come first)
      const c = v.contours[sel.ci], hs = handlesOf(c, sel.k);
      for (const hd of hs) { const d = Math.hypot(hd.p[0] - x, hd.p[1] - y); if (d < bd) { bd = d; best = { kind: "handle", ci: sel.ci, i: hd.i, which: hd.which }; } }
      if (best) return best;
    }
    v.contours.forEach((c, ci) => G.nodes(c).forEach((p, k) => { const d = Math.hypot(p[0] - x, p[1] - y); if (d < bd) { bd = d; best = { kind: "node", ci, k }; } }));
    return best;
  }
  function handlesOf(c, k) {                       // the bezier handles that belong to node k
    const out = [], n = c.segs.length;
    const into = k === 0 ? (c.closed ? n - 1 : -1) : k - 1;       // the segment that ends at the node
    if (into >= 0 && c.segs[into] && c.segs[into][0] === "C") out.push({ i: into, which: 2, p: [c.segs[into][3], c.segs[into][4]] });
    if (c.segs[k] && c.segs[k][0] === "C") out.push({ i: k, which: 1, p: [c.segs[k][1], c.segs[k][2]] });
    return out;
  }
  function setStartAt(v, x, y, tol) {
    let best = null;
    v.contours.forEach((c, ci) => { const q = G.nearest(c, x, y); if (q && (!best || q.d < best.q.d)) best = { ci, q }; });
    if (!best || best.q.d > tol) return false;
    const c = v.contours[best.ci];
    if (!c.closed) return false;                   // (only a closed cut has a start that matters)
    let c2 = c, k;
    const near = best.q.u < 0.02 ? best.q.i : best.q.u > 0.98 ? best.q.i + 1 : -1;
    if (near >= 0) k = near; else { c2 = G.insertNode(c, best.q.i, best.q.u); k = best.q.i + 1; }
    k = k % c2.segs.length;
    const cs = v.contours.slice(); cs[best.ci] = G.setStart(c2, k);
    const locked = new Set(v.locked_starts || []); locked.add(best.ci);
    edit(() => setVectors([{ ...v, contours: cs, locked_starts: [...locked] }]));
    return true;
  }

  // ------------------------------------------------------------------------------------------ snapping
  function cands(skip) { return G.snapPoints(sheet(), nodesOf, skip); }
  function gridPt(p) { const g = E.grid; return g ? [Math.round(p[0] / g) * g, Math.round(p[1] / g) * g] : p; }
  function snapPoint(p, list, tol) {
    E.mark = null;
    if (!E.snap) return p;
    const s = G.snap(p, list, tol);
    if (s) { E.mark = s; return s.p; }
    return gridPt(p);
  }

  // ------------------------------------------------------------------------------------------ the drawing and its overlay
  const ACCENT = "#f97316";
  /** the vectors, moved by (dx, dy), as lines: only what is on the screen and as much detail as its size asks for
   *  (the same rule as the drawing itself, so that a selection of all of a big drawing is as cheap as the drawing) */
  function strokeVectors(ctx, vectors, dx, dy, view, w, h) {
    const shifted = { s: view.s, ox: view.ox + dx * view.s, oy: view.oy - dy * view.s };     // (the screen, as the moved vectors see it)
    const g = B.lodGroups(vectors, shifted, w, h, () => "all").get("all");
    if (!g) return;
    ctx.save();
    if (dx || dy) ctx.translate(dx, dy);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.stroke(g.lines); ctx.fill(g.dots);
    ctx.restore();
  }
  function overlay(ctx, view, px, dark, w, h) {
    const sel = selected(), g = E.ghost;
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.strokeStyle = ACCENT; ctx.lineWidth = 2.6 * px;
    if (sel.length && !(g && g.vectors)) strokeVectors(ctx, sel, 0, 0, view, w, h);
    if (g && g.dx !== undefined) {                 // a move in progress: where it will land
      ctx.setLineDash([5 * px, 4 * px]); strokeVectors(ctx, sel, g.dx, g.dy, view, w, h); ctx.setLineDash([]);
    }
    if (g && g.vectors) strokeVectors(ctx, g.vectors, 0, 0, view, w, h);
    const bb = sel.length ? G.bbox(sel, B.flatOf) : null;
    if (bb && E.tool !== "nodes") {
      const ox = g && g.dx !== undefined ? g.dx : 0, oy = g && g.dx !== undefined ? g.dy : 0;
      ctx.strokeStyle = ACCENT; ctx.lineWidth = 1 * px; ctx.setLineDash([3 * px, 3 * px]);
      ctx.strokeRect(bb.x0 + ox, bb.y0 + oy, bb.x1 - bb.x0, bb.y1 - bb.y0); ctx.setLineDash([]);
    }
    if (E.tool === "nodes") drawNodes(ctx, px);
    if (E.rubber) drawRubber(ctx, px);
    if (E.tool === "poly" && E.poly.length) {
      ctx.strokeStyle = ACCENT; ctx.lineWidth = 2 * px; ctx.beginPath();
      E.poly.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.stroke();
      ctx.fillStyle = ACCENT; for (const [x, y] of E.poly) ctx.fillRect(x - 3 * px, y - 3 * px, 6 * px, 6 * px);
    }
    if (E.order && S.results && S.results.patch) drawOrder(ctx, view);
    if (E.mark) {                                  // what the pointer clung to
      const [x, y] = E.mark.p;
      ctx.strokeStyle = "#2563eb"; ctx.lineWidth = 1.8 * px; ctx.beginPath();
      if (E.mark.kind === "sheet") { ctx.moveTo(x - 7 * px, y); ctx.lineTo(x, y + 7 * px); ctx.lineTo(x + 7 * px, y); ctx.lineTo(x, y - 7 * px); ctx.closePath(); }
      else ctx.rect(x - 6 * px, y - 6 * px, 12 * px, 12 * px);
      ctx.stroke();
    }
  }
  /** the cut order of the calculated toolpaths: a number at the start of every cut (``order`` is a list of "vector:contour") */
  function drawOrder(ctx, view) {
    const sh = sheet(), byId = new Map(sh.vectors.map(v => [v.id, v])), seqs = S.results.patch.sequences || {};
    const dpr = window.devicePixelRatio || 1;
    ctx.save(); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.font = "bold 11px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const tp of S.project.toolpaths) {
      const order = seqs[tp.id] && seqs[tp.id].order;
      if (!order || tp.enabled === false) continue;
      const total = order.length;
      order.forEach((key, i) => {
        const [id, ci] = key.split(":"), v = byId.get(id), c = v && v.contours[+ci];
        if (!c) return;
        const x = view.ox + view.s * c.start[0], y = view.oy - view.s * c.start[1];
        ctx.fillStyle = `hsl(${240 - 240 * (total > 1 ? i / (total - 1) : 0)} 80% 45%)`;       // blue first, red last
        ctx.beginPath(); ctx.arc(x, y, 9, 0, 7); ctx.fill();
        ctx.fillStyle = "#fff"; ctx.fillText(String(i + 1), x, y + 0.5);
      });
    }
    ctx.restore();
  }
  function drawNodes(ctx, px) {
    const v = (E.ghost && E.ghost.vectors && E.ghost.vectors[0]) || oneVector();
    if (!v) return;
    v.contours.forEach((c, ci) => {
      G.nodes(c).forEach(([x, y], k) => {
        const on = E.node && E.node.ci === ci && E.node.k === k;
        ctx.fillStyle = on ? ACCENT : "#fff"; ctx.strokeStyle = on ? "#7c2d12" : "#1d4ed8"; ctx.lineWidth = 1.6 * px;
        ctx.beginPath(); ctx.rect(x - 5 * px, y - 5 * px, 10 * px, 10 * px); ctx.fill(); ctx.stroke();
        if (k === 0) { ctx.beginPath(); ctx.arc(x, y, 9 * px, 0, 7); ctx.stroke(); }          // the start of the contour
      });
      if (E.node && E.node.ci === ci) {
        const p = G.nodes(c)[E.node.k];
        if (p) for (const hd of handlesOf(c, E.node.k)) {
          ctx.strokeStyle = "#16a34a"; ctx.lineWidth = 1.2 * px; ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(hd.p[0], hd.p[1]); ctx.stroke();
          ctx.fillStyle = "#16a34a"; ctx.beginPath(); ctx.arc(hd.p[0], hd.p[1], 5 * px, 0, 7); ctx.fill();
        }
      }
    });
  }
  function drawRubber(ctx, px) {
    const r = E.rubber;
    ctx.strokeStyle = r.crossing ? "#16a34a" : "#2563eb"; ctx.lineWidth = 1.4 * px;
    if (r.kind === "box") {
      ctx.fillStyle = r.crossing ? "rgba(22,163,74,.12)" : "rgba(37,99,235,.12)";
      ctx.setLineDash(r.crossing ? [4 * px, 3 * px] : []);
      ctx.fillRect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0); ctx.strokeRect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0); ctx.setLineDash([]);
    } else if (r.contour) { ctx.strokeStyle = ACCENT; ctx.lineWidth = 2 * px; strokeContour(ctx, r.contour); }
  }
  function strokeContour(ctx, c) {
    ctx.beginPath();
    G.flatten(c).forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    if (c.closed) ctx.closePath();
    ctx.stroke();
  }
  const skipIds = () => (E.ghost && E.ghost.vectors ? new Set(E.ghost.vectors.map(v => v.id)) : null);

  // ------------------------------------------------------------------------------------------ the stage
  const TOOLS = [["view", "🖐"], ["select", "⬚"], ["move", "✥"], ["line", "／"], ["poly", "⌇"], ["rect", "▭"], ["circle", "◯"],
                 ["ellipse", "⬭"], ["polygon", "⬡"], ["nodes", "◇"], ["start", "⚑"]];
  const DRAW_TOOLS = new Set(["line", "rect", "circle", "ellipse", "polygon"]);

  function stage(sh) {
    if (E.sheetId !== sh.id) { E.sheetId = sh.id; E.sel = new Set(); E.node = null; E.poly = []; }
    keepSelection();
    const cv = h("canvas", { "aria-label": "drawing" });
    const stageEl = h("div", { class: "stage" }, cv);
    const wrap = h("div", { class: "edstage" });
    const bars = h("div", { class: "edbars" });         // (the tools and the switches: over the drawing)
    const dock = h("div", { class: "eddock" });         // (what can be done to the selection, and its coordinates: under the drawing, near the thumb and the keyboard;
                                                        //  when it comes and goes the drawing stays where it is)
    let view = S.views[sh.id], raf = 0;
    // A big drawing (tens of thousands of segments) is not drawn again at every frame of a gesture: the picture that was
    // on the screen is moved and scaled instead, and the drawing is made once more when the finger is up.
    let snap = null, gesturing = false, settle = 0;
    const heavy = () => B.sceneSegs(sh) > 12000;
    const redraw = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        if (!cv.isConnected) return;
        if (!view) refit();
        if (gesturing && snap) return drawSnap();
        B.drawScene(cv, sh, view, { skip: skipIds(), overlay });
      });
    };
    function beginGesture() {
      clearTimeout(settle);
      if (gesturing) return;
      gesturing = true;
      if (heavy()) { const c = document.createElement("canvas"); c.width = cv.width; c.height = cv.height; c.getContext("2d").drawImage(cv, 0, 0); snap = { c, v: { ...view } }; }
    }
    function endGesture(delay = 90) { clearTimeout(settle); settle = setTimeout(() => { gesturing = false; snap = null; redraw(); }, delay); }
    function drawSnap() {
      const dpr = window.devicePixelRatio || 1, ctx = cv.getContext("2d"), f = view.s / snap.v.s;
      ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, cv.width, cv.height);
      ctx.drawImage(snap.c, (view.ox - snap.v.ox * f) * dpr, (view.oy - snap.v.oy * f) * dpr, snap.c.width * f, snap.c.height * f);
      ctx.setTransform(dpr * view.s, 0, 0, -dpr * view.s, dpr * view.ox, dpr * view.oy);
      if (E.ghost || E.rubber || E.mark) overlay(ctx, view, 1 / view.s, false, cv.clientWidth, cv.clientHeight);          // (what is being drawn or moved goes on top)
    }
    /** the whole sheet in the canvas; ``view.top`` is where the canvas was on the screen, to keep the drawing still when the bars above it change */
    const refit = () => { view = S.views[sh.id] = B.fitView(cv, sh); view.top = cv.getBoundingClientRect().top; };
    const toWorld = (px, py) => [(px - view.ox) / view.s, (view.oy - py) / view.s];
    const tol = () => 14 / view.s;
    const zoomAt = (cx, cy, f) => { f = Math.min(40, Math.max(0.2, f)); view.ox = cx - (cx - view.ox) * f; view.oy = cy - (cy - view.oy) * f; view.s *= f; };
    const ptrs = new Map();
    let g = null;                                  // the one-finger gesture that is going on
    let lastGhost = null;                          // where a move in progress would land

    const hitVector = (x, y, only) => G.hit(sh.vectors, B.flatOf, x, y, tol(), v => usable(v) && (!only || only(v)));
    /** every vector under the finger, the nearest first; Alt walks through them (the one behind the one that is selected) */
    const hitStack = (x, y) => G.hitAll(sh.vectors, B.flatOf, x, y, tol(), usable);
    function tapSelect(x, y, additive, behind) {
      let id = hitVector(x, y);
      if (id && behind) { const stack = hitStack(x, y); id = G.nextAfter(stack, [...E.sel].find(i => stack.includes(i))); }
      if (id) {
        if (additive) { if (E.sel.has(id)) E.sel.delete(id); else E.sel.add(id); } else E.sel = new Set([id]);
      } else if (!additive) E.sel = new Set();
      E.node = null;
      refresh();
    }
    function refresh() { fillBars(); redraw(); }
    /** a point of the drawing: snapped to what is near, unless Alt is down (then exactly where the finger is) */
    function pick(p, list, m) {
      if (m.alt) { E.mark = null; return p; }
      return snapPoint(p, list, tol());
    }

    function start(e) {
      const [x, y] = toWorld(e.offsetX, e.offsetY), tool = E.tool, m = mods(e);
      g = { tool, sx: e.offsetX, sy: e.offsetY, x0: x, y0: y, moved: false, pan: false };
      if (tool === "view" || tool === "start") g.pan = true;
      else if (tool === "select") { g.kind = "box"; E.rubber = { kind: "box", x0: x, y0: y, x1: x, y1: y, crossing: false }; }
      else if (tool === "move") {
        const id = hitVector(x, y);
        if (id && !E.sel.has(id)) { if (!(m.shift || m.ctrl || E.multi)) E.sel = new Set([id]); else E.sel.add(id); }    // (the bars follow when the finger is up)
        if (id && E.sel.has(id)) { g.kind = "move"; g.cands = cands(E.sel); g.bb = selBox(); E.ghost = { dx: 0, dy: 0 }; lastGhost = null; } else g.pan = true;
      } else if (DRAW_TOOLS.has(tool)) { g.kind = "draw"; g.cands = cands(null); g.p0 = pick([x, y], g.cands, m); }
      else if (tool === "poly") { g.kind = "poly"; g.cands = cands(null); }
      else if (tool === "nodes") {
        const hit = nodeHit(x, y, tol());
        if (hit && hit.kind === "node") { E.node = { ci: hit.ci, k: hit.k }; g.kind = "node"; g.hit = hit; g.v = oneVector(); g.cands = cands(E.sel); fillBars(); }
        else if (hit && hit.kind === "handle") { g.kind = "handle"; g.hit = hit; g.v = oneVector(); }
        else g.pan = true;
      }
      redraw();
    }
    function drag(e) {
      const x = (e.offsetX - view.ox) / view.s, y = (view.oy - e.offsetY) / view.s, m = mods(e);
      if (Math.hypot(e.offsetX - g.sx, e.offsetY - g.sy) > 5) g.moved = true;
      if (g.pan) { return; }
      if (g.kind === "box") { Object.assign(E.rubber, { x1: x, y1: y, crossing: x < g.x0 || m.alt }); }
      else if (g.kind === "move") {
        let dx = x - g.x0, dy = y - g.y0;
        E.mark = null;
        if (m.ctrl) [dx, dy] = G.axisLock(dx, dy);                  // (Ctrl: only along one axis)
        if (E.snap && !m.alt && g.bb) {
          const feats = [];
          for (const fx of [g.bb.x0, (g.bb.x0 + g.bb.x1) / 2, g.bb.x1]) for (const fy of [g.bb.y0, (g.bb.y0 + g.bb.y1) / 2, g.bb.y1]) feats.push([fx, fy]);
          let best = null, bd = tol();
          for (const f of feats) { const s = G.snap([f[0] + dx, f[1] + dy], g.cands, bd); if (s) { const d = Math.hypot(s.p[0] - f[0] - dx, s.p[1] - f[1] - dy); if (d <= bd) { bd = d; best = { s, f }; } } }
          if (best) { dx = best.s.p[0] - best.f[0]; dy = best.s.p[1] - best.f[1]; E.mark = best.s; }
          else if (E.grid) { const q = gridPt([g.bb.x0 + dx, g.bb.y0 + dy]); dx = q[0] - g.bb.x0; dy = q[1] - g.bb.y0; }
          if (m.ctrl) [dx, dy] = G.axisLock(dx, dy);
        }
        E.ghost = { dx, dy };
      } else if (g.kind === "draw") {
        E.rubber = { kind: "shape", contour: shapeFrom(E.tool, g.p0, pick([x, y], g.cands, m), m) };
      } else if (g.kind === "poly") { E.mark = null; if (!m.alt) snapPoint([x, y], g.cands, tol()); }
      else if (g.kind === "node") {
        const c = g.v.contours[g.hit.ci], q = G.nodes(c)[g.hit.k];
        let p = pick([x, y], g.cands, m);
        if (m.ctrl) { const [lx, ly] = G.axisLock(p[0] - q[0], p[1] - q[1]); p = [q[0] + lx, q[1] + ly]; }
        const moved = G.moveNode(c, g.hit.k, p[0] - q[0], p[1] - q[1]);
        const cs = g.v.contours.slice(); cs[g.hit.ci] = moved;
        g.result = { ...g.v, contours: cs }; E.ghost = { vectors: [g.result] };
      } else if (g.kind === "handle") {
        const c = g.v.contours[g.hit.ci], s = c.segs[g.hit.i], cur = g.hit.which === 1 ? [s[1], s[2]] : [s[3], s[4]];
        let hx = x - cur[0], hy = y - cur[1];
        if (m.ctrl) [hx, hy] = G.axisLock(hx, hy);
        const moved = G.moveHandle(c, g.hit.i, g.hit.which, hx, hy);
        const cs = g.v.contours.slice(); cs[g.hit.ci] = moved;
        g.result = { ...g.v, contours: cs }; E.ghost = { vectors: [g.result] };
      }
      redraw();
    }
    function finish(e, m) {
      const gg = g; g = null;
      if (!gg) return;
      const x = (e.offsetX - view.ox) / view.s, y = (view.oy - e.offsetY) / view.s;
      E.rubber = null; E.ghost = null; E.mark = null;
      const additive = m.shift || m.ctrl || E.multi;         // (Shift and Ctrl both add to the selection, as in most programs)
      if (gg.kind === "box") {
        if (!gg.moved) tapSelect(gg.x0, gg.y0, additive, m.alt);
        else {
          const r = { x0: Math.min(gg.x0, x), y0: Math.min(gg.y0, y), x1: Math.max(gg.x0, x), y1: Math.max(gg.y0, y) }, crossing = x < gg.x0 || m.alt;
          const hits = sh.vectors.filter(v => usable(v) && G.vInRect(v, B.flatOf, r, crossing)).map(v => v.id);
          E.sel = additive ? new Set([...E.sel, ...hits]) : new Set(hits); E.node = null; refresh();
        }
      } else if (gg.kind === "move") {
        const d = lastGhost;
        if (gg.moved && d && (Math.abs(d.dx) > 1e-9 || Math.abs(d.dy) > 1e-9)) { moveSelected(d.dx, d.dy); return; }
        refresh();
      } else if (gg.kind === "draw") {
        const p1 = pick([x, y], gg.cands, m), c = shapeFrom(E.tool, gg.p0, p1, m);
        E.mark = null;
        if (c && gg.moved && Math.hypot(p1[0] - gg.p0[0], p1[1] - gg.p0[1]) > 1e-6) addVector(c, shapeName(E.tool)); else refresh();
      } else if (gg.kind === "poly") {
        if (!gg.moved) {
          let p = pick([gg.x0, gg.y0], gg.cands, m);
          if (m.ctrl && E.poly.length) { const q = E.poly[E.poly.length - 1], d = G.dragShape("line", q, p, { ctrl: true }); p = d.b; }     // (Ctrl: a side at a multiple of 15°)
          E.poly.push(p); E.mark = null;
        }
        fillBars(); redraw();
      } else if ((gg.kind === "node" || gg.kind === "handle") && gg.result) {
        if (gg.moved) replaceContours(gg.v, gg.result.contours); else refresh();
      } else if (gg.tool === "start" && !gg.moved) {
        const id = hitVector(gg.x0, gg.y0);
        const v = id && sh.vectors.find(q => q.id === id);
        if (v && setStartAt(v, gg.x0, gg.y0, tol())) { E.sel = new Set([id]); toast(t("ed.startSet"), "ok"); B.render(); } else refresh();
      } else if (gg.tool === "nodes" && !gg.moved && gg.pan) {
        const v = oneVector(), pt = [gg.x0, gg.y0];
        const onLine = v && E.insert && v.contours.map((c, ci) => ({ ci, q: G.nearest(c, pt[0], pt[1]) })).filter(o => o.q && o.q.d < tol()).sort((a, b) => a.q.d - b.q.d)[0];
        if (onLine) { const c = v.contours[onLine.ci], cs = v.contours.slice(); cs[onLine.ci] = G.insertNode(c, onLine.q.i, onLine.q.u); E.node = { ci: onLine.ci, k: onLine.q.i + 1 }; replaceContours(v, cs); }
        else tapSelect(gg.x0, gg.y0, additive, m.alt);
      } else if (gg.pan && !gg.moved) tapSelect(gg.x0, gg.y0, additive, m.alt);
      else redraw();
    }
    /** the shape of a drawing tool for a drag from a to b, as the keys change it (Ctrl: regular, Shift: from the centre) */
    function shapeFrom(tool, a, b, m) {
      const d = G.dragShape(tool, a, b, m);
      return shapeOf(tool, d.a, d.b);
    }
    function shapeOf(tool, a, b) {
      const dx = b[0] - a[0], dy = b[1] - a[1], r = Math.hypot(dx, dy);
      if (r < 1e-9) return null;
      if (tool === "line") return G.polyline([a, b], false);
      if (tool === "rect") return G.rectangle(a[0], a[1], b[0], b[1]);
      if (tool === "circle") return G.circle(a[0], a[1], r);
      if (tool === "ellipse") return Math.abs(dx) > 1e-9 && Math.abs(dy) > 1e-9 ? G.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.abs(dx) / 2, Math.abs(dy) / 2) : null;
      if (tool === "polygon") return G.polygon(a[0], a[1], r, E.sides, (Math.atan2(dy, dx) * 180 / Math.PI) - 90);
      return null;
    }
    const shapeName = tool => ({ line: "Line", rect: "Rectangle", circle: "Circle", ellipse: "Ellipse", polygon: "Polygon" }[tool] || "");

    cv.addEventListener("pointerdown", e => {
      if (!view) refit();
      try { cv.setPointerCapture(e.pointerId); } catch { /* (a pointer that is not active any more) */ }
      ptrs.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
      if (ptrs.size === 1) start(e);
      else { g = null; E.rubber = null; E.ghost = null; E.mark = null; redraw(); }
    });
    cv.addEventListener("pointermove", e => {
      const q = ptrs.get(e.pointerId);
      if (!q || !view) return;
      const nx = e.offsetX, ny = e.offsetY;
      if (ptrs.size === 2) {
        beginGesture();
        const o = [...ptrs].find(([id]) => id !== e.pointerId)[1];
        const d0 = Math.hypot(q.x - o.x, q.y - o.y) || 1, d1 = Math.hypot(nx - o.x, ny - o.y) || 1;
        zoomAt((q.x + o.x) / 2, (q.y + o.y) / 2, d1 / d0);
        view.ox += (nx + o.x) / 2 - (q.x + o.x) / 2; view.oy += (ny + o.y) / 2 - (q.y + o.y) / 2;
      } else if (g) {
        if (g.pan) { if (Math.hypot(nx - g.sx, ny - g.sy) > 5) g.moved = true; if (g.moved) { beginGesture(); view.ox += nx - q.x; view.oy += ny - q.y; } }
        else { if (Math.hypot(nx - g.sx, ny - g.sy) > 5) beginGesture(); drag(e); }
        if (E.ghost && E.ghost.dx !== undefined) lastGhost = E.ghost;
      }
      q.x = nx; q.y = ny;
      redraw();
    });
    const up = e => {
      if (!ptrs.has(e.pointerId)) return;
      const one = ptrs.size === 1;
      ptrs.delete(e.pointerId);
      if (one && g) {
        const m = mods(e), released = releaseMods();              // (a key held for one action is let go before the drawing is made again)
        finish(e, m);
        if (released) fillBars();
      } else if (!ptrs.size) { g = null; }
      if (!ptrs.size) endGesture();
    };
    cv.addEventListener("pointerup", up);
    cv.addEventListener("pointercancel", e => { ptrs.delete(e.pointerId); g = null; E.rubber = null; E.ghost = null; redraw(); endGesture(); });
    cv.addEventListener("wheel", e => { e.preventDefault(); if (view) { beginGesture(); zoomAt(e.offsetX, e.offsetY, Math.exp(-e.deltaY * 0.0015)); redraw(); endGesture(140); } }, { passive: false });
    cv.addEventListener("dblclick", () => { if (E.tool === "poly") return; refit(); redraw(); });
    // When the bars above the drawing grow or shrink (something is selected, the rows are folded) the drawing keeps its place on the
    // screen: the view moves with the top of the canvas, so that it does not jump under the finger.
    const watch = new ResizeObserver(() => {
      if (!cv.isConnected) { watch.disconnect(); return; }          // (the page was made again: this canvas is gone and must not move the view)
      const top = cv.getBoundingClientRect().top;
      if (view) {                                                    // (the view is kept between one page and the next, so is ``top``)
        if (view.top !== undefined && Math.abs(top - view.top) > 0.5) view.oy -= top - view.top;
        view.top = top;
      }
      redraw();
    });
    watch.observe(cv);

    // ---- the bars
    const btn = (label, title, fn, on, short) => {
      const lbl = E.labels && short;
      const el = h("button", { class: "ed" + (on ? " on" : "") + (lbl ? " lbl" : ""), title, "aria-label": title,
        onclick: ev => { if (el._long) { el._long = false; return; } fn(ev); } },
        lbl ? [h("span", { class: "i" }, label), h("span", { class: "l" }, short)] : label);
      let timer = 0;                              // (a long press tells what the button is: a phone has no hover)
      el.addEventListener("pointerdown", () => { el._long = false; clearTimeout(timer); timer = setTimeout(() => { el._long = true; toast(title); }, 500); });
      for (const ev of ["pointerup", "pointerleave", "pointercancel"]) el.addEventListener(ev, () => clearTimeout(timer));
      return el;
    };
    const sh_ = k => t("ed.s." + k);              // the short name under a button
    function cycleMod(k) { E.mod[k] = (E.mod[k] + 1) % 3; fillBars(); }                 // off -> held for one action -> locked -> off
    function toggleCoord() { E.coord = !E.coord; B.store.set("iyicam.coord", E.coord ? "1" : "0"); fillBars(); }
    /** the nine points of the box of the selection, to choose the one whose coordinates are typed */
    function pickRef() {
      let close = () => {};
      const grid = h("div", { class: "refgrid big", role: "radiogroup", "aria-label": t("ed.ref") }, REFS.map(([id, ic]) => h("button", {
        class: "ed" + (E.ref === id ? " on" : ""), title: t("ref." + id), "aria-label": t("ref." + id), "aria-checked": E.ref === id ? "true" : "false",
        onclick: () => { E.ref = id; B.store.set("iyicam.ref", id); if (E.tf) E.tf.ref = id; close(); fillBars(); } }, ic)));
      close = openSheet(t("ed.ref"), h("div", {}, grid, h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "grow", onclick: () => close() }, t("ed.close")))));
    }
    /** the row to type a coordinate in: where this point of the selection (a corner, a middle, the centre) goes */
    function coordRow(sel) {
      const bb = G.bbox(sel);
      if (!bb) return null;
      const [rx, ry] = refPoint(bb, E.ref);
      const refBtn = btn(REFS.find(([id]) => id === E.ref)[1], t("ed.ref") + " · " + t("ref." + E.ref), pickRef, false, sh_("ref"));
      const field = (label, v) => h("input", { type: "number", step: "any", class: "coordin", value: +v.toFixed(3), "aria-label": label, enterkeyhint: "go" });
      const X = field("X", rx), Y = field("Y", ry);
      const go = () => {
        const nx = X.valueAsNumber, ny = Y.valueAsNumber;
        if (!Number.isFinite(nx) || !Number.isFinite(ny)) return toast(t("tf.cant"), "warn");
        releaseMods();
        if (applyMap(shiftMap(nx - rx, ny - ry), 0)) toast(t("ed.moved", fmt(nx, 2), fmt(ny, 2)), "ok");
      };
      for (const el of [X, Y]) el.addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); go(); } });
      const stepIn = h("input", { type: "number", step: "any", min: "0", class: "coordin step", value: E.step, "aria-label": t("ed.step"), title: t("ed.step") });
      stepIn.addEventListener("input", () => { if (stepIn.valueAsNumber > 0) { E.step = stepIn.valueAsNumber; B.store.set("iyicam.step", String(E.step)); } });
      const nudge = (icon, dx, dy) => btn(icon, t("ed.nudge", fmt(E.step, 3)), () => {
        const k = (mods(null).shift ? 10 : 1) * E.step;           // (Shift: ten times as far)
        releaseMods();
        moveSelected(dx * k, dy * k);
      });
      const goBtn = btn("➜", t("ed.goto"), go, false, sh_("goto"));
      goBtn.classList.add("goto");
      return h("div", { class: "edrow coord" }, refBtn,
        h("span", { class: "muted small" }, "X"), X, h("span", { class: "muted small" }, "Y"), Y, goBtn,
        h("span", { class: "muted small" }, "Δ"), stepIn, nudge("◀", -1, 0), nudge("▲", 0, 1), nudge("▼", 0, -1), nudge("▶", 1, 0));
    }
    function fillBars() {
      const sel = selected(), one = oneVector();
      const toolRow = h("div", { class: "edrow tools" }, TOOLS.map(([id, ic]) => btn(ic, t("ed." + id), () => { E.tool = id; E.poly = []; E.node = null; E.insert = false; E.rubber = null; fillBars(); redraw(); }, E.tool === id, sh_(id))));
      const rows = [toolRow];
      if (E.tool === "polygon") rows.push(h("div", { class: "edrow" }, h("span", { class: "muted small" }, t("ed.sides")),
        btn("−", "−", () => { E.sides = Math.max(3, E.sides - 1); fillBars(); }), h("b", {}, String(E.sides)), btn("＋", "+", () => { E.sides = Math.min(60, E.sides + 1); fillBars(); })));
      if (E.tool === "poly") rows.push(h("div", { class: "edrow" },
        btn("✓", t("ed.done"), () => { if (E.poly.length > 1) { const c = G.polyline(E.poly, false); E.poly = []; addVector(c, "Polyline"); } }, false, sh_("done")),
        btn("▢", t("ed.node.close"), () => { if (E.poly.length > 2) { const c = G.polyline(E.poly, true); E.poly = []; addVector(c, "Polyline"); } }, false, sh_("nk")),
        btn("⌫", t("ed.last"), () => { E.poly.pop(); fillBars(); redraw(); }, false, sh_("last")), btn("✕", t("ed.cancel"), () => { E.poly = []; fillBars(); redraw(); }, false, sh_("cancel"))));
      if (E.tool === "nodes") {
        rows.push(!one ? h("div", { class: "edrow muted small" }, t("ed.node.pick")) : h("div", { class: "edrow" },
          btn("＋", t("ed.node.insert"), () => { E.insert = !E.insert; fillBars(); }, E.insert, sh_("ni")),
          btn("🗑", t("ed.node.del"), () => nodeEdit((c, k) => G.deleteNode(c, k)), false, sh_("nd")),
          btn("⚑", t("ed.node.start"), () => nodeEdit((c, k) => (c.closed ? G.setStart(c, k) : null)), false, sh_("ns")),
          btn("✂", t("ed.node.break"), () => nodeEdit((c, k) => G.breakAt(c, k)), false, sh_("nb")),
          btn("▢", t("ed.node.close"), () => nodeEdit(c => (c.closed ? null : G.closeContour(c))), false, sh_("nk")),
          btn("—", t("ed.node.line"), () => nodeEdit((c, k) => (c.segs[k] ? G.convertSpan(c, k, "L") : null)), false, sh_("nl")),
          btn("〜", t("ed.node.curve"), () => nodeEdit((c, k) => (c.segs[k] ? G.convertSpan(c, k, "C") : null)), false, sh_("nc"))));
      }
      if (!E.fold) {
        rows.push(h("div", { class: "edrow" },
          btn("↶", t("ed.undo"), undo, false, sh_("undo")), btn("↷", t("ed.redo"), redo, false, sh_("redo")),
          btn("▣", t("ed.all"), selectAll, false, sh_("all")), btn("◫", t("ed.invert"), invert, false, sh_("invert")), btn("⊘", t("ed.none"), clear, false, sh_("none")),
          btn("⌖", t("ed.transform"), openTransform, false, sh_("transform")),
          btn("▤", t("ed.layers"), openLayers, false, sh_("layers")),
          btn("⋯", t("ed.cmdsTitle"), openCommands, false, sh_("cmds")),
          btn("?", t("ed.help"), openHelp, false, sh_("help")),
          btn("Aa", t("ed.labels"), () => { E.labels = !E.labels; B.store.set("iyicam.labels", E.labels ? "1" : "0"); fillBars(); }, E.labels)));
        // the keys a phone does not have, and the switches that change how a finger works
        const key = k => {
          const name = t("ed.s." + k), st = E.mod[k], sym = { shift: "⇧", ctrl: "⌃", alt: "⌥" }[k];
          const el = btn(E.labels ? sym : name, t("ed." + k) + " · " + t(st === 2 ? "ed.mod.lock" : st === 1 ? "ed.mod.once" : "ed.mod.off", name), () => cycleMod(k), st > 0, name);
          el.setAttribute("aria-pressed", st > 0 ? "true" : "false");
          if (st === 2) el.classList.add("lock");
          return el;
        };
        rows.push(h("div", { class: "edrow keys" }, key("shift"), key("ctrl"), key("alt"),
          btn("＋", t("ed.multi"), () => { E.multi = !E.multi; fillBars(); }, E.multi, sh_("multi")),
          btn("🧲", t("ed.snap"), () => { E.snap = !E.snap; fillBars(); }, E.snap, sh_("snap")),
          btn("▦" + (E.grid ? E.grid : ""), t("ed.grid"), () => { const o = [0, 1, 5, 10, 25]; E.grid = o[(o.indexOf(E.grid) + 1) % o.length]; fillBars(); }, !!E.grid, sh_("grid"))));
      }
      const low = [];                                // (the dock stays when the rows above are folded: it is only there while something is selected)
      {
        if (sel.length) {
          const bb = G.bbox(sel);
          low.push(h("div", { class: "edrow sel" }, h("span", { class: "badge" }, t("ed.selected", sel.length) + (bb ? ` · ${fmt(bb.x1 - bb.x0, 1)}×${fmt(bb.y1 - bb.y0, 1)}` : "")),
            btn("📍", t("ed.coord"), toggleCoord, E.coord, sh_("coord")),
            btn("↻", t("ed.rot"), rot90, false, sh_("rot")), btn("⇆", t("ed.flipH"), flipH, false, sh_("flipH")), btn("⇅", t("ed.flipV"), flipV, false, sh_("flipV")), btn("⧉", t("ed.dup"), duplicate, false, sh_("dup")),
            btn("📋", t("ed.copy"), copy, false, sh_("copy")), E.clip ? btn("📌", t("ed.paste"), paste, false, sh_("paste")) : null,
            btn("🗑", t("ed.delete"), removeSelected, false, sh_("delete")), layerPicker()));
          if (E.coord) low.push(coordRow(sel));
        } else if (E.clip) low.push(h("div", { class: "edrow" }, btn("📌", t("ed.paste"), paste, false, sh_("paste"))));
      }
      bars.replaceChildren(...rows);
      dock.replaceChildren(...low);
    }
    function layerPicker() {
      const sel = h("select", { class: "ed", title: t("ed.layer"), "aria-label": t("ed.layer") }, h("option", { value: "" }, "▤ " + t("ed.layer")), S.project.layers.map(l => h("option", { value: l.id }, l.name)));
      sel.onchange = () => { if (sel.value) toLayer(sel.value); };
      return sel;
    }
    // a keyboard (a computer, or a phone with one): Ctrl, Shift and Alt are the real keys or the buttons of the bar
    const onKey = e => {
      if (!cv.isConnected) { document.removeEventListener("keydown", onKey); return; }
      if (/INPUT|TEXTAREA|SELECT/.test((e.target.tagName || ""))) return;
      const k = e.key.toLowerCase();
      if (k === "shift" || k === "control" || k === "alt" || k === "meta") return;
      const m = mods(e);
      let done = true;
      if (m.ctrl && k === "z") { e.preventDefault(); releaseMods(); m.shift ? redo() : undo(); }
      else if (m.ctrl && k === "y") { e.preventDefault(); releaseMods(); redo(); }
      else if (m.ctrl && k === "c") { releaseMods(); copy(); }
      else if (m.ctrl && k === "v") { releaseMods(); paste(); }
      else if (m.ctrl && k === "d") { e.preventDefault(); releaseMods(); duplicate(); }
      else if (m.ctrl && k === "a") { e.preventDefault(); releaseMods(); selectAll(); }
      else if (k === "delete" || k === "backspace") { releaseMods(); removeSelected(); }
      else if (k === "escape") { releaseMods(); E.poly = []; E.sel = new Set(); E.node = null; }
      else if (k.startsWith("arrow") && E.sel.size) { e.preventDefault(); releaseMods(); const s = m.shift ? 10 : 1; moveSelected(k === "arrowleft" ? -s : k === "arrowright" ? s : 0, k === "arrowup" ? s : k === "arrowdown" ? -s : 0); }
      else done = false;
      if (done) { fillBars(); redraw(); }                        // (what a key did shows in the bars and the drawing, and the keys held for it are let go)
    };
    document.addEventListener("keydown", onKey);

    const fitBtns = h("div", { class: "tools" },
      btn(E.fold ? "▾" : "▴", t("ed.fold"), () => { E.fold = !E.fold; B.store.set("iyicam.fold", E.fold ? "1" : "0"); fillBars(); fitBtns.firstChild.textContent = E.fold ? "▾" : "▴"; }),
      btn("⤢", t("draw.fit"), () => { refit(); redraw(); }),
      btn("＋", "+", () => { zoomAt(cv.clientWidth / 2, cv.clientHeight / 2, 1.5); redraw(); }),
      btn("－", "−", () => { zoomAt(cv.clientWidth / 2, cv.clientHeight / 2, 1 / 1.5); redraw(); }));
    if (S.results) {
      fitBtns.append(btn(S.showPaths ? "◉" : "○", t("draw.paths"), e => { S.showPaths = !S.showPaths; e.currentTarget.textContent = S.showPaths ? "◉" : "○"; redraw(); }));
      fitBtns.append(btn("①", t("ed.order"), e => { E.order = !E.order; e.currentTarget.classList.toggle("on", E.order); redraw(); }, E.order));
    }
    stageEl.append(fitBtns, h("div", { class: "legend" }, `${sh.name} · ${fmt(sh.width, 0)}×${fmt(sh.height, 0)} mm · ${t("proj.vectors", sh.vectors.length)}`));
    fillBars();
    wrap.append(bars, stageEl, dock);
    return wrap;
  }

  // ------------------------------------------------------------------------------------------ transform (typed numbers)
  const REFS = [["tl", "↖"], ["tc", "↑"], ["tr", "↗"], ["ml", "←"], ["mc", "●"], ["mr", "→"], ["bl", "↙"], ["bc", "↓"], ["br", "↘"]];
  /** a point of the box of the selection: t, m, b (top, middle, bottom; Y is up) and l, c, r */
  const refPoint = (bb, ref) => [ref[1] === "l" ? bb.x0 : ref[1] === "r" ? bb.x1 : (bb.x0 + bb.x1) / 2,
                                 ref[0] === "t" ? bb.y1 : ref[0] === "b" ? bb.y0 : (bb.y0 + bb.y1) / 2];
  /** the map A after the map B */
  const mul = (A, B) => [A[0] * B[0] + A[2] * B[1], A[1] * B[0] + A[3] * B[1], A[0] * B[2] + A[2] * B[3], A[1] * B[2] + A[3] * B[3],
                         A[0] * B[4] + A[2] * B[5] + A[4], A[1] * B[4] + A[3] * B[5] + A[5]];
  const shiftMap = (dx, dy) => [1, 0, 0, 1, dx, dy];
  const scaleXY = (sx, sy, cx, cy) => [sx, 0, 0, sy, cx - sx * cx, cy - sy * cy];
  const slantMap = (ax, ay, cx, cy) => { const tx = Math.tan(ax * Math.PI / 180), ty = Math.tan(ay * Math.PI / 180); return [1, ty, tx, 1, -tx * cy, -ty * cx]; };
  /** Apply a map to the selection: ``copies`` 0 changes the vectors themselves, N adds N copies (the first one moved by
   *  the map, the next by it twice, and so on: a row of five copies is one go).  ``false`` when this browser cannot (an arc
   *  under a map that does not keep its shape): the engine does it then. */
  function applyMap(map, copies) {
    const src = selected();
    if (!src.length) return null;
    if (copies > 0) {
      const news = [];
      let m = map;
      for (let k = 1; k <= Math.min(copies, 500); k++) {
        for (const v of src) { const n = G.affine(v, m); if (!n) return false; news.push({ ...n, id: G.newId(), locked_starts: (v.locked_starts || []).slice() }); }
        m = mul(map, m);
      }
      edit(() => { sheet().vectors.push(...news); });
      E.sel = new Set(news.map(v => v.id));
      return true;
    }
    const out = mapSelected(map);
    if (!out) return false;
    edit(() => setVectors(out));
    return true;
  }

  function openTransform() {
    if (!selected().length) return toast(t("tf.none"), "warn");
    const st = E.tf || (E.tf = { ref: E.ref, prop: true, copies: 0, about: "ref", px: 0, py: 0, angle: 90, ax: 15, ay: 0, sx: 100, sy: 100, dx: 0, dy: 0 });
    const body = h("div", { class: "tf" });
    let close = () => {};
    const num = (label, key, opts = {}) => h("label", { class: "f" }, h("span", {}, label), numberInput(opts.value !== undefined ? opts.value : st[key], v => { st[key] = v; if (opts.on) opts.on(v); }, opts.p));
    /** run one change: this browser's own, else the engine's command with the same numbers */
    const run = (map, fallback) => {
      const r = applyMap(map, Math.max(0, Math.round(st.copies || 0)));
      if (r === false && fallback) { const cmd = (S.schema.commands || []).find(c => c.id === fallback[0]); if (cmd) { B.guarded(async () => { await exec(cmd, fallback[1]); build(); }); return; } }
      if (r === false) toast(t("tf.cant"), "warn");
      if (r) build();
    };
    function build() {
      const sel = selected();
      if (!sel.length) { close(); return; }
      const bb = G.bbox(sel), w = bb.x1 - bb.x0, hh = bb.y1 - bb.y0, [rx, ry] = refPoint(bb, st.ref);
      const refGrid = h("div", { class: "refgrid", role: "radiogroup", "aria-label": t("tf.ref") }, REFS.map(([id, ic]) => h("button", { class: "ed" + (st.ref === id ? " on" : ""), title: t("tf.ref"), "aria-label": id, onclick: () => { st.ref = id; E.ref = id; B.store.set("iyicam.ref", id); build(); } }, ic)));
      const X = h("input", { type: "number", step: "any", value: +rx.toFixed(4) }), Y = h("input", { type: "number", step: "any", value: +ry.toFixed(4) });      // (no "decimal" keypad: it has no minus sign on some phones)
      const W = h("input", { type: "number", step: "any", inputmode: "decimal", value: +w.toFixed(4) }), H = h("input", { type: "number", step: "any", inputmode: "decimal", value: +hh.toFixed(4) });
      W.addEventListener("input", () => { if (st.prop && w > 1e-9 && Number.isFinite(W.valueAsNumber)) H.value = +(W.valueAsNumber * hh / w).toFixed(4); });
      H.addEventListener("input", () => { if (st.prop && hh > 1e-9 && Number.isFinite(H.valueAsNumber)) W.value = +(H.valueAsNumber * w / hh).toFixed(4); });
      const prop = h("input", { type: "checkbox", checked: st.prop }); prop.onchange = () => { st.prop = prop.checked; };
      const val = el => (Number.isFinite(el.valueAsNumber) ? el.valueAsNumber : 0);
      const pct = (label, key) => h("label", { class: "f" }, h("span", {}, label), numberInput(st[key], v => { st[key] = v; if (st.prop && key === "sx") st.sy = v; }));
      const aboutSel = h("select", {}, [["ref", t("tf.about.ref")], ["origin", t("tf.about.origin")], ["point", t("tf.about.point")]].map(([v, l]) => h("option", { value: v }, l)));
      aboutSel.value = st.about; aboutSel.onchange = () => { st.about = aboutSel.value; build(); };
      const pivot = () => (st.about === "origin" ? [0, 0] : st.about === "point" ? [st.px, st.py] : [rx, ry]);
      const go = (label, fn) => h("button", { class: "primary", onclick: fn }, label);
      body.replaceChildren(
        h("div", { class: "muted small" }, t("tf.sel", sel.length, fmt(bb.x0, 2), fmt(bb.y0, 2), fmt(w, 2), fmt(hh, 2))),
        h("div", { class: "group-h" }, t("tf.ref")), refGrid,
        h("div", { class: "group-h" }, t("tf.pos")),
        h("div", { class: "grid2" }, h("label", { class: "f" }, h("span", {}, "X"), X), h("label", { class: "f" }, h("span", {}, "Y"), Y)),
        go(t("tf.go"), () => run(shiftMap(val(X) - rx, val(Y) - ry))),
        h("div", { class: "group-h" }, t("tf.by")),
        h("div", { class: "grid2" }, num("dX", "dx"), num("dY", "dy")),
        go(t("tf.move"), () => run(shiftMap(st.dx, st.dy), ["xform.move", { dx: st.dx, dy: st.dy }])),
        h("div", { class: "group-h" }, t("tf.size")),
        h("div", { class: "grid2" }, h("label", { class: "f" }, h("span", {}, t("tf.w")), W), h("label", { class: "f" }, h("span", {}, t("tf.h")), H)),
        h("label", { class: "chk" }, prop, t("tf.prop")),
        go(t("tf.sizeGo"), () => {
          const nw = val(W), nh = val(H);
          if (nw <= 0 || nh <= 0 || w < 1e-9 || hh < 1e-9) return toast(t("tf.cant"), "warn");
          run(scaleXY(nw / w, nh / hh, rx, ry), ["xform.size", { width: nw, height: nh, uniform: false }]);
        }),
        h("div", { class: "group-h" }, t("tf.scale")),
        h("div", { class: "grid2" }, pct(t("tf.sx"), "sx"), pct(t("tf.sy"), "sy")),
        go(t("tf.scaleGo"), () => {
          const sx = st.sx / 100, sy = (st.prop ? st.sx : st.sy) / 100;
          if (!(sx > 0 && sy > 0)) return toast(t("tf.cant"), "warn");
          run(scaleXY(sx, sy, rx, ry), ["xform.scale", { sx: st.sx, sy: st.prop ? st.sx : st.sy, uniform: false, about: "center" }]);
        }),
        h("div", { class: "group-h" }, t("tf.rot")),
        h("div", { class: "grid2" }, num(t("tf.angle"), "angle"), h("label", { class: "f" }, h("span", {}, t("tf.about")), aboutSel)),
        st.about === "point" ? h("div", { class: "grid2" }, num("X", "px"), num("Y", "py")) : null,
        go(t("tf.rotGo"), () => { const [cx, cy] = pivot(); run(G.rotateMap(st.angle, cx, cy)); }),
        h("div", { class: "group-h" }, t("tf.mirror")),
        h("div", { class: "row" },
          h("button", { class: "grow", onclick: () => run(G.flipHMap(pivot()[0]), ["xform.mirror", { axis: "vertical", about: "center" }]) }, "⇆ " + t("tf.mh")),
          h("button", { class: "grow", onclick: () => run(G.flipVMap(pivot()[1]), ["xform.mirror", { axis: "horizontal", about: "center" }]) }, "⇅ " + t("tf.mv"))),
        h("div", { class: "group-h" }, t("tf.slant")),
        h("div", { class: "grid2" }, num(t("tf.ax"), "ax"), num(t("tf.ay"), "ay")),
        go(t("tf.slantGo"), () => { const [cx, cy] = pivot(); run(slantMap(st.ax, st.ay, cx, cy), ["xform.slant", { angle_x: st.ax, angle_y: st.ay, about: "center" }]); }),
        h("div", { class: "group-h" }, t("tf.copies")),
        num(t("tf.copiesN"), "copies", { p: { kind: "int", min: 0 } }),
        h("div", { class: "row", style: "margin-top:14px" }, h("button", { class: "grow", onclick: () => close() }, t("ed.close"))));
    }
    build();
    close = openSheet(t("tf.title"), body);
  }

  // ------------------------------------------------------------------------------------------ what every button is
  function openHelp() {
    const item = (ic, key, ...a) => h("div", { class: "helprow" }, h("span", { class: "helpic" }, ic), h("span", {}, t(key, ...a)));
    const sec = (title, items) => h("div", {}, h("div", { class: "group-h" }, title), items.map(([ic, key, ...a]) => item(ic, key, ...a)));
    let close;
    close = openSheet(t("help.title"), h("div", { class: "help" },
      h("div", { class: "muted small" }, t("help.tip")),
      sec(t("help.tools"), TOOLS.map(([id, ic]) => [ic, "ed." + id])),
      sec(t("help.general"), [["↶", "ed.undo"], ["↷", "ed.redo"], ["▣", "ed.all"], ["◫", "ed.invert"], ["⊘", "ed.none"], ["⌖", "ed.transform"],
        ["▤", "ed.layers"], ["⋯", "ed.cmdsTitle"], ["?", "ed.help"], ["Aa", "ed.labels"], ["▴", "ed.fold"]]),
      sec(t("help.mods"), [["⇧", "ed.shift"], ["⌃", "ed.ctrl"], ["⌥", "ed.alt"], ["＋", "ed.multi"], ["🧲", "ed.snap"], ["▦", "ed.grid"]]),
      sec(t("help.coord"), [["📍", "ed.coord"], ["↙", "ed.ref"], ["➜", "ed.goto"], ["◀▲", "ed.nudge", "1"], ["Δ", "ed.step"]]),
      sec(t("help.selection"), [["↻", "ed.rot"], ["⇆", "ed.flipH"], ["⇅", "ed.flipV"], ["⧉", "ed.dup"], ["📋", "ed.copy"], ["📌", "ed.paste"], ["🗑", "ed.delete"], ["▤", "ed.layer"]]),
      sec(t("help.nodes"), [["＋", "ed.node.insert"], ["🗑", "ed.node.del"], ["⚑", "ed.node.start"], ["✂", "ed.node.break"], ["▢", "ed.node.close"], ["—", "ed.node.line"], ["〜", "ed.node.curve"]]),
      sec(t("help.poly"), [["✓", "ed.done"], ["⌫", "ed.last"], ["✕", "ed.cancel"]]),
      sec(t("help.side"), [["⤢", "draw.fit"], ["＋", "help.zoomin"], ["－", "help.zoomout"], ["◉", "draw.paths"], ["①", "ed.order"]]),
      h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "grow", onclick: () => close() }, t("ed.close")))));
  }

  // ------------------------------------------------------------------------------------------ the engine's commands
  async function exec(cmd, values) {
    const before = snapshot();
    const r = await runJob("command", { project: S.project, command: cmd.id, sheet: sheet().id, selection: [...E.sel], values, active_layer: activeLayer().id });
    if (!r.changed) { E.sel = new Set(r.selection); toast(r.messages[0] || t("ed.nochange"), "warn"); B.render(); return; }
    E.undo.push(before); E.redo = []; trimUndo();
    S.project = r.project; S.sheetId = S.project.sheets.some(x => x.id === S.sheetId) ? S.sheetId : S.project.sheets[0].id;
    E.sel = new Set(r.selection); E.node = null;
    B.changed();
    toast(r.messages.length ? r.messages.slice(0, 3).join(" · ") : t("ed.cmdDone", cmd.label.replace(/\.\.\.$/, "")), "ok");
  }
  function openForm(cmd) {
    const need = cmd.min_selection || 0;
    if (E.sel.size < need) return toast(t("ed.minsel", need), "warn");
    const label = cmd.label.replace(/\.\.\.$/, "");
    if (!cmd.params.length) return B.guarded(() => exec(cmd, {}));
    const vals = {};
    const choices = cmd.params.map(p => (p.name === "layer" && p.kind === "choice" && (!p.choices || !p.choices.length)) ? { ...p, choices: S.project.layers.map(l => [l.name, l.name]) } : p);
    let close;
    const body = h("div", {}, paramsForm(choices, vals, S.schema.tools),
      h("div", { class: "row", style: "margin-top:16px" },
        h("button", { class: "primary grow", onclick: () => { close(); B.guarded(() => exec(cmd, vals)); } }, t("ed.apply")),
        h("button", { onclick: () => close() }, t("ed.cancel"))));
    close = openSheet(label, body);
  }
  function openCommands() {
    const cmds = S.schema.commands || [];
    const search = h("input", { type: "search", placeholder: t("ed.search"), "aria-label": t("ed.search") });
    const list = h("div", { class: "cmdlist" });
    let close;
    const build = () => {
      const q = search.value.trim().toLowerCase(), groups = new Map();
      for (const c of cmds) {
        if (q && !c.label.toLowerCase().includes(q) && !c.id.includes(q)) continue;
        const g = (c.menu || "").split("/").slice(-1)[0] || "Other";
        if (!groups.has(g)) groups.set(g, []);
        groups.get(g).push(c);
      }
      const els = [];
      for (const [g, list2] of groups) {
        els.push(h("details", { open: !!q || g === "Transform" || g === "Create" }, h("summary", {}, `${g} (${list2.length})`),
          h("div", { class: "cmdgrid" }, list2.map(c => {
            const off = E.sel.size < (c.min_selection || 0);
            return h("button", { class: "cmd" + (off ? " off" : ""), title: c.tooltip || c.id, onclick: () => { close(); openForm(c); } }, c.label.replace(/\.\.\.$/, ""));
          }))));
      }
      els.push(h("details", {}, h("summary", {}, t("nest.title")), h("div", { class: "cmdgrid" }, h("button", { class: "cmd", onclick: () => { close(); nestDialog(); } }, "🧩 " + t("nest.title")))));
      list.replaceChildren(...els);
    };
    search.addEventListener("input", build);
    build();
    close = openSheet(t("ed.cmdsTitle") + (E.sel.size ? ` · ${t("ed.selected", E.sel.size)}` : ""), h("div", {}, search, list, h("div", { class: "row", style: "margin-top:12px" }, h("button", { onclick: () => close() }, t("ed.close")))));
  }

  // ------------------------------------------------------------------------------------------ nesting
  /** The result of a nesting becomes the project (one step to undo).  ``before``: the project as it was (default: as it is). */
  function applyNest(r, before) {
    if (S.project) { E.undo.push(before || snapshot()); E.redo = []; trimUndo(); }       // (no project open, a result that comes later: nothing to go back to)
    S.project = r.project; S.sheetId = S.project.sheets[0].id; E.sel = new Set(); S.views = {};
    B.changed();
    const n = x => (Array.isArray(x) ? x.length : (+x || 0));
    toast(t("nest.result", n(r.placed), n(r.unplaced), S.project.sheets.length), "ok");
  }
  B.jobApply.nest = r => applyNest(r);                  // (jobs.js applies the result of a nesting asked for again)
  const nestState = Object.assign({ arrange: "free", corner: "bl", gravity: "auto" }, JSON.parse(B.store.get("iyicam.nest") || "null") || { margin: 10, spacing: 8, rotation: 90, iterations: 8, copies: 1, squeeze: 0 });
  /** what the engine is asked: the plate, the gaps, the turning, and how the parts are put down (``arrange``: free, or in rows
   *  along X / columns along Y from a corner; free nesting may be kept to one direction) */
  function nestSettings(s) {
    const o = { width: s.width, height: s.height, margin: s.margin, spacing: s.spacing, rotation: s.rotation, iterations: s.iterations, squeeze_angle: s.squeeze || 0,
                arrange: s.arrange || "free" };
    if (o.arrange !== "free") o.corner = s.corner || "bl";
    else if (s.gravity && s.gravity !== "auto") { o.gravity = s.gravity; o.gravity_only = true; }
    return o;
  }
  function nestDialog() {
    const sh = sheet(), s = nestState;
    s.width = s.width || sh.width; s.height = s.height || sh.height;
    const only = h("input", { type: "checkbox", checked: E.sel.size > 0 });
    const num = (label, key, p) => h("label", { class: "f" }, h("span", {}, label), numberInput(s[key], v => { s[key] = v; }, p || {}));
    const pick = (label, key, options, on) => {
      const sel = h("select", {}, options.map(([v, l]) => h("option", { value: v }, l)));
      sel.value = s[key];
      sel.onchange = () => { s[key] = sel.value; if (on) on(); };
      return h("label", { class: "f" }, h("span", {}, label), sel);
    };
    const cornerBox = h("div"), gravBox = h("div");
    const showArrange = () => { cornerBox.hidden = s.arrange === "free"; gravBox.hidden = s.arrange !== "free"; };
    cornerBox.append(pick(t("nest.corner"), "corner", [["bl", t("nest.c.bl")], ["br", t("nest.c.br")], ["tl", t("nest.c.tl")], ["tr", t("nest.c.tr")]]));
    gravBox.append(pick(t("nest.gravity"), "gravity", [["auto", t("nest.g.auto")], ["left", t("nest.g.left")], ["bottom", t("nest.g.bottom")], ["compact", t("nest.g.compact")],
                                                        ["reach_x", t("nest.g.reach_x")], ["reach_y", t("nest.g.reach_y")], ["diag", t("nest.g.diag")]]));
    const arrangeBox = h("div", {}, pick(t("nest.arrange"), "arrange", [["free", t("nest.a.free")], ["rows", t("nest.a.rows")], ["columns", t("nest.a.columns")]], showArrange), cornerBox, gravBox);
    showArrange();
    let close;
    const run = () => B.guarded(async () => {
      B.store.set("iyicam.nest", JSON.stringify(s));
      const ids = only.checked ? [...E.sel] : null;
      if (only.checked && !ids.length) return toast(t("nest.nothing"), "warn");
      const quantities = {};
      if (s.copies > 1) for (const v of S.project.sheets.flatMap(x => x.vectors)) if (!ids || ids.includes(v.id)) quantities[v.id] = Math.round(s.copies);
      const before = snapshot();
      let r;
      try {
        // a kept job: it goes on at the server whatever happens to this page (the connection, the browser closed or asleep) and
        // is one of the last three jobs, to be asked for again (jobs.js); a result that comes after the connection was lost is
        // offered, not taken: the person is asked first
        r = await runJob("nest", { project: S.project, objects: ids, quantities,
          settings: nestSettings(s) },
          { keep: { title: `${t("nest.title")} · ${S.project.name || ""}`.trim() }, deferrable: true });
      } catch (e) {
        if (e instanceof B.JobDeferred) { if (B.jobs) B.jobs.offer(e.id); return; }
        throw e;
      }
      applyNest(r, before);
    });
    close = openSheet(t("nest.title"), h("div", {},
      h("div", { class: "group-h" }, t("nest.plate")), h("div", { class: "grid2" }, num(t("nest.w"), "width"), num(t("nest.h"), "height")),
      h("div", { class: "grid2" }, num(t("nest.margin"), "margin"), num(t("nest.spacing"), "spacing")),
      h("div", { class: "grid2" }, num(t("nest.rot"), "rotation"), num(t("nest.copies"), "copies", { kind: "int", min: 1 })),
      h("div", { class: "grid2" }, num(t("nest.iter"), "iterations", { kind: "int", min: 1 }), num(t("nest.squeeze"), "squeeze")),
      h("div", { class: "group-h" }, t("nest.how")), arrangeBox,
      h("label", { class: "chk" }, only, t("nest.sel")),
      h("div", { class: "row", style: "margin-top:16px" }, h("button", { class: "primary grow", onclick: () => { close(); run(); } }, t("nest.run")), h("button", { onclick: () => close() }, t("ed.cancel")))));
  }

  // ------------------------------------------------------------------------------------------ layers (Project tab and the window of the drawing)
  const LAYER_VIEWS = new Set();                    // (every list of layers that is on the page: the card, the window; all follow a change)
  function refreshLayers() {
    for (const v of [...LAYER_VIEWS]) { if (v.el.isConnected) v.build(); else LAYER_VIEWS.delete(v); }
  }
  /** What a change of a layer needs besides the project: the drawing is made again (the Project tab shows none) and the other lists follow. */
  function layerChanged(redraw) {
    B.persist();
    if (redraw && S.tab === "draw") B.render();
    refreshLayers();
  }
  /** all the vectors of a layer on this sheet become the selection (Shift, Ctrl or the multi button add to what was selected); the drawing is shown */
  function selectLayer(l) {
    if (!l.visible || l.locked) return toast(t("lay.hidden"), "warn");
    const ids = sheet().vectors.filter(v => v.layer === l.id).map(v => v.id);
    if (!ids.length) return toast(t("lay.empty"), "warn");
    const m = mods(null), add = m.shift || m.ctrl || E.multi;
    releaseMods();
    E.sel = new Set(add ? [...E.sel, ...ids] : ids); E.node = null;
    E.sheetId = sheet().id;                          // (a drawing that is opened for the first time on this sheet would clear the selection)
    toast(t("lay.selected", ids.length, l.name), "ok");
    S.tab = "draw"; B.render();
    return true;
  }
  /** a layer one place up (d = -1) or down (d = 1) in the list, as the desktop's up and down buttons do */
  function moveLayer(l, d) {
    const ls = S.project.layers, i = ls.indexOf(l), j = i + d;
    if (i < 0 || j < 0 || j >= ls.length) return;
    remember(); [ls[i], ls[j]] = [ls[j], ls[i]];
    layerChanged(true);
  }
  function addLayer() {
    const p = S.project;
    remember();
    const palette = S.schema.layer_colors || ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e"];
    p.layers.push({ id: G.newId(), name: `${t("lay.name")} ${p.layers.length + 1}`, color: palette[p.layers.length % palette.length], visible: true, locked: false });
    B.changed(); refreshLayers();
  }
  /** the list of layers: what is drawn on, name, colour, show, lock, select all of it, up, down, delete.  ``after`` runs when a layer was selected. */
  function layerList(after) {
    const el = h("div", { class: "list layers" });
    function build() {
      const p = S.project, here = new Map(), all = new Map(), act = activeLayer().id;
      for (const v of sheet().vectors) here.set(v.layer, (here.get(v.layer) || 0) + 1);
      for (const s of p.sheets) for (const v of s.vectors) all.set(v.layer, (all.get(v.layer) || 0) + 1);
      el.replaceChildren(...p.layers.map((l, i) => {
        const vis = h("button", { class: "small tgl" + (l.visible ? " on" : ""), title: t("lay.vis"), "aria-label": t("lay.vis"), "aria-pressed": l.visible ? "true" : "false" }, l.visible ? "👁" : "🚫");
        vis.onclick = () => { l.visible = !l.visible; if (!l.visible) for (const id of [...E.sel]) { const v = sheet().vectors.find(q => q.id === id); if (v && v.layer === l.id) E.sel.delete(id); } layerChanged(true); };
        const name = h("input", { type: "text", value: l.name, "aria-label": t("lay.name") });
        name.addEventListener("change", () => { if (name.value.trim()) { remember(); l.name = name.value.trim(); layerChanged(true); } });
        const color = h("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(l.color) ? l.color : "#1f77b4", "aria-label": "color", class: "swatch" });
        color.onchange = () => { remember(); l.color = color.value; layerChanged(true); };
        const lock = h("button", { class: "small tgl" + (l.locked ? " on" : ""), title: t("lay.lock"), "aria-label": t("lay.lock"), "aria-pressed": l.locked ? "true" : "false" }, l.locked ? "🔒" : "🔓");
        lock.onclick = () => { l.locked = !l.locked; for (const id of [...E.sel]) { const v = sheet().vectors.find(q => q.id === id); if (v && v.layer === l.id && l.locked) E.sel.delete(id); } layerChanged(true); };
        const del = h("button", { class: "small danger", title: t("lay.del"), "aria-label": t("lay.del"), onclick: () => {
          if (p.layers.length < 2) return toast(t("lay.last"), "warn");
          if (all.get(l.id)) return toast(t("lay.notEmpty"), "warn");
          remember(); p.layers = p.layers.filter(x => x.id !== l.id);
          for (const tp of p.toolpaths) if (tp.selection && tp.selection.layers) tp.selection.layers = tp.selection.layers.filter(x => x !== l.id);
          B.changed(); refreshLayers();
        } }, "🗑");
        const pick = h("button", { class: "small", title: t("lay.select"), "aria-label": t("lay.select"), onclick: () => { if (selectLayer(l) && after) after(); } }, "◎");
        const up = h("button", { class: "small", title: t("lay.up"), "aria-label": t("lay.up"), disabled: i === 0, onclick: () => moveLayer(l, -1) }, "↑");
        const down = h("button", { class: "small", title: t("lay.down"), "aria-label": t("lay.down"), disabled: i === p.layers.length - 1, onclick: () => moveLayer(l, 1) }, "↓");
        const on = h("input", { type: "radio", name: "activelayer", checked: l.id === act, title: t("lay.active"), "aria-label": t("lay.active") });
        on.onchange = () => { E.layer = l.id; };
        return h("div", { class: "lay", "data-layer": l.id },
          h("div", { class: "l1" }, on, color, h("div", { class: "grow" }, name), h("span", { class: "badge", title: t("proj.vectors", all.get(l.id) || 0) }, here.get(l.id) || 0)),
          h("div", { class: "l2" }, vis, lock, h("span", { class: "grow" }), pick, up, down, del));
      }));
    }
    const view = { el, build };
    LAYER_VIEWS.add(view);
    build();
    return el;
  }
  function layersCard() {
    return h("div", { class: "card" }, h("h2", {}, t("proj.layers")), h("div", { class: "muted small" }, t("lay.hint")), layerList(),
      h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "small", onclick: addLayer }, "＋ " + t("lay.add"))));
  }
  /** the layers over the drawing: the same list as the Project tab has, so that a layer can be selected or moved without leaving the drawing */
  function openLayers() {
    let close = () => {};
    close = openSheet(t("lay.title"), h("div", {}, h("div", { class: "muted small" }, t("lay.hint")), layerList(() => close()),
      h("div", { class: "row", style: "margin-top:10px" }, h("button", { onclick: addLayer }, "＋ " + t("lay.add")), h("button", { class: "grow", onclick: () => close() }, t("ed.close")))));
  }
  function sheetTools() {
    const p = S.project;
    const add = h("button", { class: "small", onclick: () => {
      const cur = sheet();
      remember(); const s = { id: G.newId(), name: `Sheet ${p.sheets.length + 1}`, width: cur.width, height: cur.height, thickness: null, vectors: [], outline: null };
      p.sheets.push(s); S.sheetId = s.id; B.changed();
    } }, "＋ " + t("sheet.add"));
    const del = h("button", { class: "small danger", onclick: () => {
      if (p.sheets.length < 2) return toast(t("sheet.last"), "warn");
      if (!confirm(t("sheet.delask"))) return;
      remember(); const id = sheet().id; p.sheets = p.sheets.filter(s => s.id !== id); S.sheetId = p.sheets[0].id;
      for (const tp of p.toolpaths) if (tp.sheets) tp.sheets = tp.sheets.filter(i => i !== id);
      B.changed();
    } }, t("sheet.del"));
    const name = h("input", { type: "text", value: sheet().name, "aria-label": t("paths.name") });
    name.addEventListener("change", () => { if (name.value.trim()) { remember(); sheet().name = name.value.trim(); B.persist(); } });
    return h("div", { class: "row", style: "margin-top:10px" }, h("div", { class: "grow" }, name), add, del);
  }
  async function newProject() {
    const r = await api("validate", { project: { format: "iyicam-project", version: 1, name: t("proj.newname") } });
    S.project = r.project; S.sheetId = S.project.sheets[0].id; S.views = {}; S.report = null; S.results = S.gcode = S.sim = null;
    E.undo = []; E.redo = []; E.sel = new Set(); B.setFile(null);
    S.tab = "draw"; B.changed();
  }

  B.editor = { stage, layersCard, sheetTools, newProject, undo, redo, openCommands, nestDialog, openLayers, selectLayer, moveLayer, mods, state: E, clear: () => { E.undo = []; E.redo = []; E.sel = new Set(); E.node = null; } };
})();
