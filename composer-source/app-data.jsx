/* ───────────── DATA LAYER (ported from v2) ───────────── */
/* Provides: DEFAULT_PRODUCTS, getDefaultState, createBlock, LANGS, LANG_LABELS.
   Also replaces the v3 mock globals PRODUCTS, PREWRITTEN_TEXTS, TEMPLATES,
   STANDALONE_BLOCKS with the richer v2 data so existing v3 UI keeps working. */

const LANGS = ['es','fr','de','en','nl']
const LANG_LABELS = {es:'ES 🇪🇸',fr:'FR 🇫🇷',de:'DE 🇩🇪',en:'EN 🇬🇧',nl:'NL 🇳🇱'}

const DEFAULT_PRODUCTS = [
  { id:"young", brand:"artisjet", name:"artisJet Young", badge:"A4", badgeBg:"#dcfce7", badgeColor:"#15803d", img:"https://artisjet-printers.eu/wp-content/uploads/2020/10/Imagen-102.png", desc:"Compacta para personalización en punto de venta, stands y start-ups.", area:"20 × 30 cm", alt:"5 cm", feat1:"Sensor de altura automático", feat2:"CMYK + Blanco", price:"9.300 €", link:"https://boprint.net/producto/artis-young-uv-led/", accent:"#1d4ed8", gradient:"linear-gradient(90deg,#1d4ed8,#3b82f6)", visible:true, i18n:{fr:{desc:"Compacte pour la personnalisation au point de vente, stands et start-ups.",feat1:"Capteur de hauteur automatique",feat2:"CMYK + Blanc",price:"9 300 €",link:"https://artisjet-printers.eu/shop/artisjet-young/",badge:"A4"},de:{desc:"Kompakt für Anpassung am Point of Sale, Stands und Start-ups.",feat1:"Automatische Höhenerkennung",feat2:"CMYK + Weiß",price:"9.500 €",link:"https://artisjet-printers.eu/de/shop/artisjet-young/",badge:"A4"},en:{desc:"Compact for point-of-sale customization, stands and start-ups.",feat1:"Automatic height sensor",feat2:"CMYK + White",price:"€9,500",link:"https://www.mqeurope.com/product/artisjet-young/",badge:"A4"},nl:{desc:"Compact voor aanpassingen op het verkooppunt, stands en start-ups.",feat1:"Automatische hoogtesensor",feat2:"CMYK + Wit",price:"€9.500",link:"https://www.mqeurope.com/nl/product/artisjet-young/",badge:"A4"}} },
  { id:"3000pro", brand:"artisjet", name:"artisJet 3000Pro Freebird", badge:"Freebird", badgeBg:"#dbeafe", badgeColor:"#1d4ed8", img:"https://artisjet-printers.eu/wp-content/uploads/2025/02/3000-pro-freebirdok.png", desc:"Flatbed pro con Freebird. Velocidad 2,5× y certificación Braille/ADA.", area:"35 × 50 cm", alt:"17 cm", feat1:"Módulo Freebird integrado", feat2:"Mesa de vacío", price:"13.900 €", link:"https://boprint.net/producto/3000-pro-freebird/", accent:"#1d4ed8", gradient:"linear-gradient(90deg,#1d4ed8,#3b82f6)", visible:true, i18n:{fr:{desc:"Flatbed pro avec Freebird. Vitesse 2,5× et certification Braille/ADA.",feat1:"Module Freebird intégré",feat2:"Table de vide",price:"14 500 €",link:"https://artisjet-printers.eu/shop/artisjet-3000pro-freebird-led-uv/",badge:"Freebird"},de:{desc:"Flatbed Pro mit Freebird. 2,5× Geschwindigkeit und Braille/ADA-Zertifizierung.",feat1:"Integriertes Freebird-Modul",feat2:"Vakuumtisch",price:"13.900 €",link:"https://artisjet-printers.eu/de/shop/artisjet-3000pro-freebird-led-uv/",badge:"Freebird"},en:{desc:"Pro flatbed with Freebird. 2.5x speed and Braille/ADA certification.",feat1:"Integrated Freebird module",feat2:"Vacuum table",price:"€14,500",link:"https://www.mqeurope.com/product/artisjet-3000pro-freebird-led-uv/",badge:"Freebird"},nl:{desc:"Pro flatbed met Freebird. 2,5× snelheid en Braille/ADA-certificering.",feat1:"Geïntegreerde Freebird-module",feat2:"Vacuümtafel",price:"€14.500",link:"https://www.mqeurope.com/nl/product/artisjet-3000pro-freebird-led-uv/",badge:"Freebird"}} },
  { id:"proud", brand:"artisjet", name:"artisJet Proud", badge:"Tarjetas", badgeBg:"#fef3c7", badgeColor:"#92400e", img:"https://artisjet-printers.eu/wp-content/uploads/2024/05/artisJet-proud-card-printer-left-side-view.jpeg", desc:"Especialista en tarjetas, carnets y artículos promocionales.", area:"-", alt:"-", feat1:"Alta precisión piezas pequeñas", feat2:"CR80 y tarjetas grandes", price:"21.500 €", link:"https://boprint.net/producto/artisjet-proud/", accent:"#1d4ed8", gradient:"linear-gradient(90deg,#1d4ed8,#3b82f6)", visible:true, i18n:{fr:{desc:"Spécialiste des cartes, carnets et articles promotionnels.",feat1:"Haute précision petites pièces",feat2:"CR80 et grandes cartes",price:"21 500 €",link:"https://artisjet-printers.eu/shop/artisjet-proud/",badge:"Cartes"},de:{desc:"Spezialist für Karten, Ausweise und Werbeartikel.",feat1:"Hochpräzision kleine Teile",feat2:"CR80 und große Karten",price:"21.500 €",link:"https://artisjet-printers.eu/de/shop/artisjet-proud/",badge:"Karten"},en:{desc:"Specialist in cards, badges and promotional items.",feat1:"High precision small parts",feat2:"CR80 and large cards",price:"€21,500",link:"https://www.mqeurope.com/product/artisjet-proud/",badge:"Cards"},nl:{desc:"Specialist in kaarten, badges en promotiebijdragen.",feat1:"Hoge precisie kleine onderdelen",feat2:"CR80 en grote kaarten",price:"€21.500",link:"https://www.mqeurope.com/nl/product/artisjet-proud/",badge:"Kaarten"}} },
  { id:"5000u", brand:"artisjet", name:"artisJet 5000U", badge:"A3+ Freebird", badgeBg:"#e0e7ff", badgeColor:"#4338ca", img:"https://artisjet-printers.eu/wp-content/uploads/2017/11/artis5000Uimg1.jpg", desc:"Formato medio para volúmenes crecientes y objetos 3D.", area:"A3+", alt:"-", feat1:"Velocidad profesional", feat2:"CMYK + Blanco", price:"18.900 €", link:"https://boprint.net/producto/5000-uv-led/", accent:"#1d4ed8", gradient:"linear-gradient(90deg,#1d4ed8,#3b82f6)", visible:true, i18n:{fr:{desc:"Format moyen pour les volumes croissants et les objets 3D.",feat1:"Vitesse professionnelle",feat2:"CMYK + Blanc",price:"18 500 €",link:"https://artisjet-printers.eu/shop/artis-5000u-uv-led/",badge:"A3+ Freebird"},de:{desc:"Mittleres Format für wachsende Volumen und 3D-Objekte.",feat1:"Professionelle Geschwindigkeit",feat2:"CMYK + Weiß",price:"17.500 €",link:"https://artisjet-printers.eu/de/shop/artis-5000u-uv-led/",badge:"A3+ Freebird"},en:{desc:"Medium format for growing volumes and 3D objects.",feat1:"Professional speed",feat2:"CMYK + White",price:"€19,500",link:"https://www.mqeurope.com/product/artis-5000-uv-led-a2/",badge:"A3+ Freebird"},nl:{desc:"Middenformaat voor groeiende volumes en 3D-objecten.",feat1:"Professionele snelheid",feat2:"CMYK + Wit",price:"€19.500",link:"https://www.mqeurope.com/nl/product/artis-5000-uv-led-a2/",badge:"A3+ Freebird"}} },
  { id:"6090trust", brand:"artisjet", name:"artisJet 6090 Trust", badge:"Gran Formato", badgeBg:"#fee2e2", badgeColor:"#991b1b", img:"https://artisjet-printers.eu/wp-content/uploads/2024/08/trust-freebird.png", desc:"Producción industrial: señalización, placas, paneles decorativos.", area:"59 × 89 cm", alt:"10 cm", feat1:"Freebird integrado", feat2:"Alta productividad", price:"20.900 €", link:"https://boprint.net/producto/artisjet-6090-uv-led/", accent:"#1d4ed8", gradient:"linear-gradient(90deg,#1d4ed8,#3b82f6)", visible:true, i18n:{fr:{desc:"Production industrielle: signalétique, plaques, panneaux décoratifs.",feat1:"Freebird intégré",feat2:"Haute productivité",price:"19 900 €",link:"https://artisjet-printers.eu/shop/artisjet-6090-led-uv/",badge:"Grand Format"},de:{desc:"Industrielle Produktion: Beschilderung, Platten, Dekorpaneele.",feat1:"Integrierter Freebird",feat2:"Hohe Produktivität",price:"19.900 €",link:"https://artisjet-printers.eu/de/shop/artisjet-6090-led-uv/",badge:"Großformat"},en:{desc:"Industrial production: signage, plaques, decorative panels.",feat1:"Integrated Freebird",feat2:"High productivity",price:"€20,900",link:"https://www.mqeurope.com/product/artisjet-6090-led-uv/",badge:"Large Format"},nl:{desc:"Industriële productie: bewegwijzering, platen, decoratieve panelen.",feat1:"Geïntegreerde Freebird",feat2:"Hoge productiviteit",price:"€20.900",link:"https://www.mqeurope.com/nl/product/artisjet-6090-led-uv/",badge:"Groot Formaat"}} },
  { id:"mbo3050", brand:"mbo", name:"MBO 3050", badge:"Nuevo", badgeBg:"#dcfce7", badgeColor:"#15803d", img:"https://mboprinters.com/wp-content/uploads/2026/02/mbo3050.png", desc:"Compacta multifuncional, la más reciente de MBO.", area:"30 × 50 cm", alt:"10 cm", feat1:"Sensor 1-3 mm", feat2:"Tanques 250 cc", price:"6.495 €", link:"https://mboprinters.com/product/mbo-3050-uv-led", accent:"#6d28d9", gradient:"linear-gradient(90deg,#6d28d9,#8b5cf6)", visible:true, i18n:{fr:{desc:"Compacte multifonction, la plus récente de MBO.",feat1:"Capteur 1-3 mm",feat2:"Réservoirs 250 cc",price:"6 495 €",link:"https://mboprinters.com/fr/product/mbo-3050-uv-led",badge:"Nouveau"},de:{desc:"Multifunktionales Kompaktmodell, das neueste von MBO.",feat1:"Sensor 1-3 mm",feat2:"Tanks 250 cc",price:"6.495 €",link:"https://mboprinters.com/de/product/mbo-3050-uv-led",badge:"Neu"},en:{desc:"Multifunctional compact, the latest from MBO.",feat1:"1-3 mm sensor",feat2:"250 cc tanks",price:"€6,495",link:"https://mboprinters.com/en/product/mbo-3050-uv-led",badge:"New"},nl:{desc:"Multifunctioneel compact, het nieuwste van MBO.",feat1:"1-3 mm sensor",feat2:"250 cc tanks",price:"€6.495",link:"https://mboprinters.com/en/product/mbo-3050-uv-led",badge:"Nieuw"}} },
  { id:"mbo4060", brand:"mbo", name:"MBO 4060", badge:"Top ventas", badgeBg:"#ede9fe", badgeColor:"#6d28d9", img:"https://mboprinters.com/wp-content/uploads/2022/02/mbo4060-1.jpg", desc:"El mejor ratio calidad-precio del mercado.", area:"33 × 55 cm", alt:"20 cm", feat1:"Pantalla táctil", feat2:"Control inteligente", price:"7.995 €", link:"https://mboprinters.com/product/mbo-4060-uv-led", accent:"#6d28d9", gradient:"linear-gradient(90deg,#6d28d9,#8b5cf6)", visible:true, i18n:{fr:{desc:"Le meilleur rapport qualité-prix du marché.",feat1:"Écran tactile",feat2:"Contrôle intelligent",price:"7 995 €",link:"https://mboprinters.com/fr/product/mbo-4060-uv-led",badge:"Top ventes"},de:{desc:"Das beste Preis-Leistungs-Verhältnis auf dem Markt.",feat1:"Touchscreen",feat2:"Intelligente Kontrolle",price:"7.995 €",link:"https://mboprinters.com/de/product/mbo-4060-uv-led",badge:"Top Verkauf"},en:{desc:"Best quality-to-price ratio on the market.",feat1:"Touchscreen",feat2:"Intelligent control",price:"€7,995",link:"https://mboprinters.com/en/product/mbo-4060-uv-led",badge:"Best seller"},nl:{desc:"Beste kwaliteit-prijs verhouding op de markt.",feat1:"Touchscreen",feat2:"Intelligent control",price:"€7.995",link:"https://mboprinters.com/en/product/mbo-4060-uv-led",badge:"Best seller"}} },
  { id:"mbo6090", brand:"mbo", name:"MBO 6090", badge:"Semiformato", badgeBg:"#ede9fe", badgeColor:"#6d28d9", img:"https://mboprinters.com/wp-content/uploads/2022/02/mbo6090.jpg", desc:"Producción profesional multicolor. 3 cabezales.", area:"60 × 90 cm", alt:"20 cm", feat1:"CMYK LC LM+W+Barniz", feat2:"Servomotor", price:"14.500 €", link:"https://mboprinters.com/product/mbo-6090-uv-led", accent:"#6d28d9", gradient:"linear-gradient(90deg,#6d28d9,#8b5cf6)", visible:true, i18n:{fr:{desc:"Production professionnelle multicolore. 3 têtes.",feat1:"CMYK LC LM+W+Vernis",feat2:"Servomoteur",price:"14 500 €",link:"https://mboprinters.com/fr/product/mbo-6090-uv-led",badge:"Demi-format"},de:{desc:"Professionelle Mehrfarbenproduktion. 3 Köpfe.",feat1:"CMYK LC LM+W+Lack",feat2:"Servomotor",price:"14.500 €",link:"https://mboprinters.com/de/product/mbo-6090-uv-led",badge:"Halbformat"},en:{desc:"Professional multicolor production. 3 heads.",feat1:"CMYK LC LM+W+Varnish",feat2:"Servomotor",price:"€14,500",link:"https://mboprinters.com/en/product/mbo-6090-uv-led",badge:"Mid-format"},nl:{desc:"Professionele meerkleurenproductie. 3 koppen.",feat1:"CMYK LC LM+W+Vernis",feat2:"Servomotor",price:"€14.500",link:"https://mboprinters.com/en/product/mbo-6090-uv-led",badge:"Halfformaat"}} },
  { id:"mbo1015", brand:"mbo", name:"MBO 1015", badge:"Industrial", badgeBg:"#fee2e2", badgeColor:"#b91c1c", img:"https://mboprinters.com/wp-content/uploads/2022/02/mbo1015.jpg", desc:"Gran formato para tiradas grandes.", area:"100 × 150 cm", alt:"10 cm", feat1:"Epson i3200", feat2:"CMYK+WWWW+Barniz", price:"desde 21.500 €", link:"https://mboprinters.com/product/mbo-1015-uv-led", accent:"#6d28d9", gradient:"linear-gradient(90deg,#6d28d9,#8b5cf6)", visible:true, i18n:{fr:{desc:"Grand format pour les grands tirages.",feat1:"Epson i3200",feat2:"CMYK+WWWW+Vernis",price:"ab 21 500 €",link:"https://mboprinters.com/fr/product/mbo-1015-uv-led",badge:"Industriel"},de:{desc:"Großformat für Großauflagen.",feat1:"Epson i3200",feat2:"CMYK+WWWW+Lack",price:"ab 21.500 €",link:"https://mboprinters.com/de/product/mbo-1015-uv-led",badge:"Industrie"},en:{desc:"Large format for large print runs.",feat1:"Epson i3200",feat2:"CMYK+WWWW+Varnish",price:"from €21,500",link:"https://mboprinters.com/en/product/mbo-1015-uv-led",badge:"Industrial"},nl:{desc:"Groot formaat voor grote oplagen.",feat1:"Epson i3200",feat2:"CMYK+WWWW+Vernis",price:"vanaf €21.500",link:"https://mboprinters.com/en/product/mbo-1015-uv-led",badge:"Industrieel"}} },
  { id:"uv1612g", brand:"mbo", name:"MBO UV1612G", badge:"Gran Formato", badgeBg:"#fee2e2", badgeColor:"#b91c1c", img:"https://mboprinters.com/wp-content/uploads/2024/11/mbo1612g.jpg", desc:"Industrial de alta velocidad. Acero reforzado.", area:"160 × 120 cm", alt:"20 cm", feat1:"Múltiples cabezales", feat2:"Alta velocidad", price:"desde 26.500 €", link:"https://mboprinters.com/product/mbo-uv1612g", accent:"#6d28d9", gradient:"linear-gradient(90deg,#6d28d9,#8b5cf6)", visible:true, i18n:{fr:{desc:"Industriel haute vitesse. Acier renforcé.",feat1:"Plusieurs têtes",feat2:"Haute vitesse",price:"à partir de 26 500 €",link:"https://mboprinters.com/fr/product/mbo-uv1612g",badge:"Grand Format"},de:{desc:"Hochgeschwindigkeits-Industrie. Verstärkter Stahl.",feat1:"Mehrere Köpfe",feat2:"Hohe Geschwindigkeit",price:"ab 26.500 €",link:"https://mboprinters.com/de/product/mbo-uv1612g",badge:"Großformat"},en:{desc:"High-speed industrial. Reinforced steel.",feat1:"Multiple heads",feat2:"High speed",price:"from €26,500",link:"https://mboprinters.com/en/product/mbo-uv1612g",badge:"Large Format"},nl:{desc:"Industrieel hoge snelheid. Versterkt staal.",feat1:"Meerdere koppen",feat2:"Hoge snelheid",price:"vanaf €26.500",link:"https://mboprinters.com/en/product/mbo-uv1612g",badge:"Groot Formaat"}} },
  { id:"uv1812", brand:"mbo", name:"MBO UV1812", badge:"Gran Formato", badgeBg:"#fee2e2", badgeColor:"#b91c1c", img:"https://mboprinters.com/wp-content/uploads/2024/11/mbo1812.jpg", desc:"Displays y publicidad. 180×120cm.", area:"180 × 120 cm", alt:"20 cm", feat1:"CMYK+W+Barniz", feat2:"PVC, madera, metal, vidrio", price:"desde 27.500 €", link:"https://mboprinters.com/product/mbo-uv1812", accent:"#6d28d9", gradient:"linear-gradient(90deg,#6d28d9,#8b5cf6)", visible:true, i18n:{fr:{desc:"Affichages et publicité. 180×120cm.",feat1:"CMYK+W+Vernis",feat2:"PVC, bois, métal, verre",price:"à partir de 27 500 €",link:"https://mboprinters.com/fr/product/mbo-uv1812",badge:"Grand Format"},de:{desc:"Displays und Werbung. 180×120cm.",feat1:"CMYK+W+Lack",feat2:"PVC, Holz, Metall, Glas",price:"ab 27.500 €",link:"https://mboprinters.com/de/product/mbo-uv1812",badge:"Großformat"},en:{desc:"Displays and advertising. 180×120cm.",feat1:"CMYK+W+Varnish",feat2:"PVC, wood, metal, glass",price:"from €27,500",link:"https://mboprinters.com/en/product/mbo-uv1812",badge:"Large Format"},nl:{desc:"Displays en advertenties. 180×120cm.",feat1:"CMYK+W+Vernis",feat2:"PVC, hout, metaal, glas",price:"vanaf €27.500",link:"https://mboprinters.com/en/product/mbo-uv1812",badge:"Groot Formaat"}} },
  { id:"uv2513", brand:"mbo", name:"MBO UV2513", badge:"Súper Gran Formato", badgeBg:"#fee2e2", badgeColor:"#b91c1c", img:"https://mboprinters.com/wp-content/uploads/2024/11/mbo2513.jpg", desc:"Producción masiva. 250×130cm.", area:"250 × 130 cm", alt:"20 cm", feat1:"Cabezales alta velocidad", feat2:"Rígidos y flexibles", price:"desde 34.000 €", link:"https://mboprinters.com/product/mbo-uv2513", accent:"#6d28d9", gradient:"linear-gradient(90deg,#6d28d9,#8b5cf6)", visible:true, i18n:{fr:{desc:"Production massive. 250×130cm.",feat1:"Têtes haute vitesse",feat2:"Rigides et flexibles",price:"à partir de 34 000 €",link:"https://mboprinters.com/fr/product/mbo-uv2513",badge:"Super Grand Format"},de:{desc:"Massenproduktion. 250×130cm.",feat1:"Hochgeschwindigkeitsköpfe",feat2:"Starr und flexibel",price:"ab 34.000 €",link:"https://mboprinters.com/de/product/mbo-uv2513",badge:"Super Großformat"},en:{desc:"Mass production. 250×130cm.",feat1:"High-speed heads",feat2:"Rigid and flexible",price:"from €34,000",link:"https://mboprinters.com/en/product/mbo-uv2513",badge:"Super Large Format"},nl:{desc:"Massaproductie. 250×130cm.",feat1:"Snelle koppen",feat2:"Stijf en flexibel",price:"vanaf €34.000",link:"https://mboprinters.com/en/product/mbo-uv2513",badge:"Super Groot Formaat"}} },
  { id:"casebox", brand:"pimpam", name:"PimPam CaseBox", badge:"Interior · Retail", badgeBg:"#ffedd5", badgeColor:"#9a3412", img:"https://pimpam-vending.com/wp-content/uploads/2026/01/pimpam-aeropuerto.png", desc:"Máquina compacta de interior para centros comerciales y aeropuertos.", area:"-", alt:"-", feat1:"UV en TPU/Silicona/PC", feat2:"Múltiples formas de pago", price:"Consultar", link:"https://pimpam-vending.com/casebox-vending/", accent:"#ea580c", gradient:"linear-gradient(90deg,#ea580c,#f97316)", visible:true, i18n:{fr:{desc:"Machine compacte intérieure pour les centres commerciaux et les aéroports.",feat1:"UV en TPU/Silicone/PC",feat2:"Plusieurs modes de paiement",price:"Sur demande",link:"https://pimpam-vending.com/casebox-vending/",badge:"Intérieur · Retail"},de:{desc:"Kompakte Innenmaschine für Einkaufszentren und Flughäfen.",feat1:"UV auf TPU/Silikon/PC",feat2:"Mehrere Zahlungsmethoden",price:"Auf Anfrage",link:"https://pimpam-vending.com/casebox-vending/",badge:"Innen · Einzelhandel"},en:{desc:"Compact indoor machine for shopping centers and airports.",feat1:"UV on TPU/Silicone/PC",feat2:"Multiple payment methods",price:"On request",link:"https://pimpam-vending.com/casebox-vending/",badge:"Interior · Retail"},nl:{desc:"Compacte binnenmaschine voor winkelcentra en luchthavens.",feat1:"UV op TPU/Silicone/PC",feat2:"Meerdere betaalmethoden",price:"Op aanvraag",link:"https://pimpam-vending.com/casebox-vending/",badge:"Binnen · Retail"}} },
  { id:"custom", brand:"pimpam", name:"PimPam Custom", badge:"Tu marca", badgeBg:"#ffedd5", badgeColor:"#9a3412", img:"https://pimpam-vending.com/wp-content/uploads/2026/01/custom-pimpam-851x1024.png", desc:"Branding completo: vinilos, pantalla y campañas con tu marca.", area:"-", alt:"-", feat1:"Vinilos personalizados", feat2:"Campañas temporales", price:"Consultar", link:"https://pimpam-vending.com/custom/", accent:"#ea580c", gradient:"linear-gradient(90deg,#ea580c,#f97316)", visible:true, i18n:{fr:{desc:"Branding complet: vinyls, écran et campagnes avec votre marque.",feat1:"Vinyls personnalisés",feat2:"Campagnes temporelles",price:"Sur demande",link:"https://pimpam-vending.com/custom/",badge:"Votre marque"},de:{desc:"Vollständiges Branding: Vinyls, Bildschirm und Kampagnen mit Ihrer Marke.",feat1:"Personalisierte Vinyls",feat2:"Zeitliche Kampagnen",price:"Auf Anfrage",link:"https://pimpam-vending.com/custom/",badge:"Ihre Marke"},en:{desc:"Complete branding: vinyls, screen and campaigns with your brand.",feat1:"Custom vinyls",feat2:"Temporary campaigns",price:"On request",link:"https://pimpam-vending.com/custom/",badge:"Your brand"},nl:{desc:"Compleet branding: vinyls, scherm en campagnes met uw merk.",feat1:"Aangepaste vinyls",feat2:"Tijdelijke campagnes",price:"Op aanvraag",link:"https://pimpam-vending.com/custom/",badge:"Uw merk"}} },
  /* ── SmartJet FLEX ── */
  { id:"flexone", brand:"smartjet", name:"SmartJet FLEX ONE", badge:"Entry Level", badgeBg:"#dcfce7", badgeColor:"#15803d", img:"https://boprint.net/wp-content/uploads/2025/12/BB2_Pocket_Shopify_desktop-2.png", desc:"La más accesible. Diseño compacto, uso intuitivo, calidad sólida para packaging.", area:"297 mm ancho", alt:"35 cm", feat1:"1200 ppp · HP PageWide", feat2:"27 m/min · INTEGRA", price:"desde 1.000 €/mes", link:"https://boprint.net/producto/flex-one/", accent:"#0d9488", gradient:"linear-gradient(90deg,#0d9488,#14b8a6)", visible:true, i18n:{en:{desc:"Most accessible. Compact design, intuitive use, solid quality for packaging.",feat1:"1200 dpi · HP PageWide",feat2:"27 m/min · INTEGRA",price:"from €1,000/mo",link:"https://boprint.net/producto/flex-one/",badge:"Entry Level"},fr:{desc:"La plus accessible. Design compact, usage intuitif, qualité solide pour le packaging.",feat1:"1200 ppp · HP PageWide",feat2:"27 m/min · INTEGRA",price:"à partir de 1 000 €/mois",link:"https://boprint.net/producto/flex-one/",badge:"Entrée de gamme"},de:{link:"https://boprint.net/producto/flex-one/"},nl:{link:"https://boprint.net/producto/flex-one/"}} },
  { id:"flex297", brand:"smartjet", name:"SmartJet FLEX 297", badge:"Versátil", badgeBg:"#ccfbf1", badgeColor:"#0d9488", img:"https://boprint.net/wp-content/uploads/2025/12/flex297-cover.png", desc:"Versátil y robusta. Equilibrio perfecto entre calidad, inversión y facilidad de operación.", area:"297 mm ancho", alt:"35 cm", feat1:"1200 ppp · HP PageWide", feat2:"27 m/min · INTEGRA BASIC 24\"", price:"Consultar", link:"https://boprint.net/producto/flex-297/", accent:"#0d9488", gradient:"linear-gradient(90deg,#0d9488,#14b8a6)", visible:true, i18n:{en:{desc:"Versatile and robust. Perfect balance between quality, investment and ease of operation.",feat1:"1200 dpi · HP PageWide",feat2:"27 m/min · INTEGRA BASIC 24\"",price:"On request",link:"https://boprint.net/producto/flex-297/",badge:"Versatile"},fr:{desc:"Polyvalente et robuste. Équilibre parfait entre qualité, investissement et facilité d'utilisation.",feat1:"1200 ppp · HP PageWide",feat2:"27 m/min · INTEGRA BASIC 24\"",price:"Sur demande",link:"https://boprint.net/producto/flex-297/",badge:"Polyvalente"},de:{link:"https://boprint.net/producto/flex-297/"},nl:{link:"https://boprint.net/producto/flex-297/"}} },
  { id:"flexultra", brand:"smartjet", name:"SmartJet FLEX ULTRA", badge:"Avanzada", badgeBg:"#cffafe", badgeColor:"#0e7490", img:"https://boprint.net/wp-content/uploads/2025/12/flex-ultra-1600x1184-1.png", desc:"Calidad premium. Pantalla INTEGRA PRO 32\", analítica de producción y control avanzado.", area:"297 mm ancho", alt:"35 cm", feat1:"1200 ppp · INTEGRA PRO 32\"", feat2:"Analítica y control avanzado", price:"Consultar", link:"https://boprint.net/producto/flex-ultra/", accent:"#0d9488", gradient:"linear-gradient(90deg,#0d9488,#14b8a6)", visible:true, i18n:{en:{desc:"Premium quality. 32\" INTEGRA PRO screen, production analytics and advanced control.",feat1:"1200 dpi · INTEGRA PRO 32\"",feat2:"Analytics and advanced control",price:"On request",link:"https://boprint.net/producto/flex-ultra/",badge:"Advanced"},fr:{desc:"Qualité premium. Écran INTEGRA PRO 32\", analytique de production et contrôle avancé.",feat1:"1200 ppp · INTEGRA PRO 32\"",feat2:"Analytique et contrôle avancé",price:"Sur demande",link:"https://boprint.net/producto/flex-ultra/",badge:"Avancée"},de:{link:"https://boprint.net/producto/flex-ultra/"},nl:{link:"https://boprint.net/producto/flex-ultra/"}} },
  { id:"flex324", brand:"smartjet", name:"SmartJet FLEX 324", badge:"Alta Producción", badgeBg:"#fee2e2", badgeColor:"#991b1b", img:"https://boprint.net/wp-content/uploads/2025/12/flex324.png", desc:"Máxima capacidad. 1600 ppp, 46 m/min. Para entornos industriales de gran volumen.", area:"324 mm ancho", alt:"35 cm", feat1:"1600 ppp · HP PageWide", feat2:"46 m/min · INTEGRA", price:"Consultar", link:"https://boprint.net/categoria-producto/smartjet-flex/", accent:"#0d9488", gradient:"linear-gradient(90deg,#0d9488,#14b8a6)", visible:true, i18n:{en:{desc:"Maximum capacity. 1600 dpi, 46 m/min. For high-volume industrial environments.",feat1:"1600 dpi · HP PageWide",feat2:"46 m/min · INTEGRA",price:"On request",link:"https://boprint.net/categoria-producto/smartjet-flex/",badge:"High Production"},fr:{desc:"Capacité maximale. 1600 ppp, 46 m/min. Pour environnements industriels à haut volume.",feat1:"1600 ppp · HP PageWide",feat2:"46 m/min · INTEGRA",price:"Sur demande",link:"https://boprint.net/categoria-producto/smartjet-flex/",badge:"Haute Production"},de:{link:"https://boprint.net/categoria-producto/smartjet-flex/"},nl:{link:"https://boprint.net/categoria-producto/smartjet-flex/"}} },
]

function getDefaultState() {
  return {
    brands: [
      {id:'artisjet', label:'artisJet', logo:'https://artisjet-printers.eu/wp-content/uploads/2017/12/logoartisjet.jpg', url:{es:'https://boprint.net',fr:'https://artisjet-printers.eu',de:'https://artisjet-printers.eu/de',en:'https://www.mqeurope.com',nl:'https://www.mqeurope.com/nl'}, urlLabel:{es:'boprint.net →',fr:'artisjet-printers.eu →',de:'artisjet-printers.eu →',en:'mqeurope.com →',nl:'mqeurope.com →'}, color:'#2563eb', divider:'#e2e8f0', logoHeight:'18', visible:true, logoText:'artisJet'},
      {id:'mbo', label:'MBO UV-LED', logo:'https://mboprinters.com/wp-content/uploads/2022/10/logomboprinterslongb.png', url:{es:'https://mboprinters.com',fr:'https://mboprinters.com/fr',de:'https://mboprinters.com/de',en:'https://mboprinters.com/en',nl:'https://mboprinters.com/en'}, urlLabel:{es:'mboprinters.com →',fr:'mboprinters.com →',de:'mboprinters.com →',en:'mboprinters.com →',nl:'mboprinters.com →'}, color:'#7c3aed', divider:'#e2e8f0', logoHeight:'18', visible:true, logoText:'MBO UV-LED'},
      {id:'mbo_dtf', label:'MBO DTF', logo:'https://mboprinters.com/wp-content/uploads/2022/10/logomboprinterslongb.png', url:{es:'https://mboprinters.com/dtf',fr:'https://mboprinters.com/fr/dtf',de:'https://mboprinters.com/de/dtf',en:'https://mboprinters.com/en/dtf',nl:'https://mboprinters.com/en/dtf'}, urlLabel:{es:'mboprinters.com →',fr:'mboprinters.com →',de:'mboprinters.com →',en:'mboprinters.com →',nl:'mboprinters.com →'}, color:'#db2777', divider:'#fce7f3', logoHeight:'18', visible:true, logoText:'MBO DTF'},
      {id:'pimpam', label:'PimPam Vending', logo:'https://pimpam-vending.com/wp-content/uploads/2025/11/WhatsApp_Image_2025-11-14_at_10.12.57-removebg-preview-e1763541822221.png', url:{es:'https://pimpam-vending.com',fr:'https://pimpam-vending.com',de:'https://pimpam-vending.com',en:'https://pimpam-vending.com',nl:'https://pimpam-vending.com'}, urlLabel:{es:'pimpam-vending.com →',fr:'pimpam-vending.com →',de:'pimpam-vending.com →',en:'pimpam-vending.com →',nl:'pimpam-vending.com →'}, color:'#ea580c', divider:'#fed7aa', logoHeight:'22', visible:true, logoText:'PimPam'},
      {id:'smartjet', label:'SmartJet', logo:'https://boprint.net/wp-content/uploads/2025/12/smartjet_prodotti-1.png', url:{es:'https://boprint.net/categoria-producto/smartjet-flex/',fr:'https://boprint.net/categoria-producto/smartjet-flex/',de:'https://boprint.net/categoria-producto/smartjet-flex/',en:'https://boprint.net/categoria-producto/smartjet-flex/',nl:'https://boprint.net/categoria-producto/smartjet-flex/'}, urlLabel:{es:'boprint.net →',fr:'boprint.net →',de:'boprint.net →',en:'boprint.net →',nl:'boprint.net →'}, color:'#0d9488', divider:'#ccfbf1', logoHeight:'22', logoMaxWidth:'180', visible:true, logoText:'SmartJet'},
      {id:'flux', label:'FLUX', logo:'', url:{es:'',fr:'',de:'',en:'',nl:''}, urlLabel:{es:'flux →',fr:'flux →',de:'flux →',en:'flux →',nl:'flux →'}, color:'#64748b', divider:'#e2e8f0', logoHeight:'18', visible:true, logoText:'FLUX'},
      // Categorías de muestras (sin URLs / logos — funcionan como tags
      // para clasificar imágenes de muestras de impresión por tipo de
      // tecnología). Color/divider distintos para que se distingan
      // claramente del resto en la biblioteca de imágenes y en filtros.
      {id:'muestras_uv', label:'Muestras UV', logo:'', url:{es:'',fr:'',de:'',en:'',nl:''}, urlLabel:{es:'',fr:'',de:'',en:'',nl:''}, color:'#0891b2', divider:'#cffafe', logoHeight:'18', visible:true, logoText:'Muestras UV'},
      {id:'muestras_laser', label:'Muestras Laser', logo:'', url:{es:'',fr:'',de:'',en:'',nl:''}, urlLabel:{es:'',fr:'',de:'',en:'',nl:''}, color:'#dc2626', divider:'#fee2e2', logoHeight:'18', visible:true, logoText:'Muestras Laser'},
      {id:'muestras_textil', label:'Muestras Textil', logo:'', url:{es:'',fr:'',de:'',en:'',nl:''}, urlLabel:{es:'',fr:'',de:'',en:'',nl:''}, color:'#16a34a', divider:'#dcfce7', logoHeight:'18', visible:true, logoText:'Muestras Textil'},
      {id:'bomedia', label:'Bomedia', logo:'', url:{es:'https://bomedia.es',fr:'https://bomedia.es',de:'https://bomedia.es',en:'https://bomedia.es',nl:'https://bomedia.es'}, urlLabel:{es:'bomedia.es →',fr:'bomedia.es →',de:'bomedia.es →',en:'bomedia.es →',nl:'bomedia.es →'}, color:'#1a1918', divider:'#e2e8f0', logoHeight:'18', visible:true, logoText:'bomedia'},
    ],
    products: DEFAULT_PRODUCTS.map(function(p){ return Object.assign({}, p); }),
    composedBlocks: [
      {id:'block-001',title:'Entry Level MBO 3050+4060',desc:'Compactas MBO con mejor precio',priceRange:'desde 6.495 € + 7.995 €',colorTag:'purple',introText:'Si buscas empezar con impresión UV-LED sin una inversión inicial grande, estas dos compactas de nuestra marca MBO son la mejor opción del mercado. Ideales para negocios que quieren probar la tecnología o complementar su producción:',brandStrip:'mbo',blockType:'product_pair',products:['mbo3050','mbo4060'],includeHero:false,includeSteps:false,visible:true,i18n:{fr:{introText:"Pour démarrer l'impression UV-LED sans gros investissement, ces deux compactes de notre marque MBO offrent le meilleur rapport qualité-prix du marché. Ideales pour tester la technologie ou compléter votre production :"},de:{introText:'Wenn Sie mit UV-LED-Druck starten möchten, ohne viel zu investieren: Diese beiden Kompaktmodelle unserer Marke MBO bieten das beste Preis-Leistungs-Verhältnis am Markt. Ideal zum Einstieg oder als Ergänzung:'},en:{introText:'Looking to get started with UV-LED printing without a large upfront investment? These two compact machines from our MBO brand offer the best value on the market. Ideal for testing the technology or complementing your production:'},nl:{introText:'Wilt u starten met UV-LED-printen zonder grote investering? Deze twee compacte machines van ons merk MBO bieden de beste prijs-kwaliteitverhouding op de markt. Ideaal om de technologie te testen of uw productie aan te vullen:'}}},
      {id:'block-002',title:'artisJet Compactas Young+3000Pro',desc:'Personalización a producción pro',priceRange:'9.300 € + 13.900 €',colorTag:'blue',introText:'Nuestra gama compacta de artisJet cubre desde la personalización en punto de venta (Young) hasta la producción profesional con Freebird para objetos 3D (3000Pro). Ambas ocupan poco espacio y son fáciles de integrar en cualquier entorno de trabajo:',brandStrip:'artisjet',blockType:'product_pair',products:['young','3000pro'],includeHero:false,includeSteps:false,visible:true,i18n:{fr:{introText:'Notre gamme compacte artisJet couvre de la personnalisation en point de vente (Young) à la production professionnelle avec Freebird pour objets 3D (3000Pro). Peu encombrantes et faciles à intégrer dans tout environnement :'},de:{introText:'Unsere kompakte artisJet-Reihe deckt alles ab — von der Personalisierung am Point of Sale (Young) bis zur professionellen Produktion mit Freebird für 3D-Objekte (3000Pro). Platzsparend und einfach zu integrieren:'},en:{introText:'Our compact artisJet range covers everything from point-of-sale customization (Young) to professional production with Freebird for 3D objects (3000Pro). Both are compact and easy to integrate into any workspace:'},nl:{introText:'Ons compacte artisJet-assortiment dekt alles — van personalisatie op het verkooppunt (Young) tot professionele productie met Freebird voor 3D-objecten (3000Pro). Compact en makkelijk te integreren:'}}},
      {id:'block-003',title:'artisJet Producción Proud+5000U',desc:'Tarjetas y formato A3+',priceRange:'21.500 € + 18.900 €',colorTag:'blue',introText:'Para entornos que requieren producción continua o especialización: la Proud está diseñada para tarjetas, carnets y artículos promocionales en volúmenes industriales, y la 5000U es el salto a formato A3+ para negocios en crecimiento:',brandStrip:'artisjet',blockType:'product_pair',products:['proud','5000u'],includeHero:false,includeSteps:false,visible:true},
      {id:'block-004',title:'artisJet Medio+Gran Formato 5000U+6090Trust',desc:'Formato A3+ e industrial',priceRange:'18.900 € + 20.900 €',colorTag:'blue',introText:'Cuando necesitas saltar a formatos mayores sin perder versatilidad. La 5000U es perfecta para personalización A3+ y la 6090 Trust para producción industrial de señalización, placas y paneles decorativos. Velocidad, precisión y productividad industrial:',brandStrip:'artisjet',blockType:'product_pair',products:['5000u','6090trust'],includeHero:false,includeSteps:false,visible:true},
      {id:'block-005',title:'MBO Semiformato+Industrial 6090+1015',desc:'Producción profesional multicolor',priceRange:'14.500 € + desde 21.500 €',colorTag:'purple',introText:'La pareja de MBO para producción a escala profesional. La 6090 es el punto de entrada a formato grande con 3 cabezales, y la 1015 es el caballo de batalla para tiradas industriales de gran formato. CMYK+W+Barniz disponibles:',brandStrip:'mbo',blockType:'product_pair',products:['mbo6090','mbo1015'],includeHero:false,includeSteps:false,visible:true},
      {id:'block-006',title:'Mix Formato Medio 3000Pro+MBO6090',desc:'Cross-brand: versatilidad artisJet + potencia MBO',priceRange:'13.900 € + 14.500 €',colorTag:'gray',introText:'Una combinación híbrida perfecta para estudios que necesitan versatilidad (la 3000Pro con Freebird) + producción en volumen (la MBO 6090). Dos filosofías de impresión complementarias, diferente software pero máxima cobertura de mercado:',brandStrip:'none',blockType:'product_pair',products:['3000pro','mbo6090'],includeHero:false,includeSteps:false,visible:true},
      {id:'block-007',title:'Mix Gran Formato 6090Trust+MBO1015',desc:'industrial: artisJet premium + MBO heavy duty',priceRange:'20.900 € + desde 21.500 €',colorTag:'gray',introText:'La élite del gran formato: la 6090 Trust de artisJet (con Freebird integrado) para máxima precisión + la MBO 1015 para volumen bruto. Dos máquinas de la liga de 100 × 150 cm, filosofías complementarias, presupuesto robusto:',brandStrip:'none',blockType:'product_pair',products:['6090trust','mbo1015'],includeHero:false,includeSteps:false,visible:true},
      {id:'block-008',title:'MBO Gran Formato Industrial UV1612G+UV1812+UV2513',desc:'Línea de producción masiva',priceRange:'desde 26.500 € + 27.500 € + 34.000 €',colorTag:'purple',introText:'Cuando necesitas línea de producción real: el dúo UV1612G+UV1812 para volumen medio-alto, o el salto a la UV2513 para producción masiva (250×130 cm). Cabezales de alta velocidad, rígidos y flexibles, CMYK+W+Barniz. Esto es factorización industrial:',brandStrip:'mbo',blockType:'product_trio',products:['uv1612g','uv1812','uv2513'],includeHero:false,includeSteps:false,visible:true},
      {id:'block-009',title:'PimPam Vending CaseBox+Custom',desc:'Autoservicio impreso 24/7',priceRange:'Consultar',colorTag:'orange',introText:'PimPam es el vending con personalización UV-LED integrada. Sin operario. El cliente elige, personaliza su funda, paga y se la lleva en 30 segundos. CaseBox para retail/interiores, Custom para branding completo con tus vinilos y campañas temporales:',brandStrip:'pimpam',blockType:'product_pair',products:['casebox','custom'],includeHero:true,includeSteps:false,visible:true},
      {id:'block-010',title:'Complemento Vending artisJet Young',desc:'Impresión manual + autoservicio híbrido',priceRange:'9.300 €',colorTag:'orange',introText:'Para negocios que quieren combinar: una Young en mostrador (operario imprime en el acto) + una PimPam en zona común (autoservicio). Máxima flexibilidad, dos modelos de negocio en paralelo, tiempos rápidos, márgenes altos:',brandStrip:'none',blockType:'product_single',products:['young'],includeHero:false,includeSteps:false,visible:true},
      {id:'block-011',title:'Proceso PimPam 4 Pasos',desc:'El viaje del cliente en 30 segundos',priceRange:'-',colorTag:'orange',introText:'Esto es lo que ve el cliente: selecciona diseño, personaliza, paga, y se va con su funda. Sin esperas, sin complicaciones, sin personal. El "por qué" de PimPam. Aquí se convierte en negocio rentable.',brandStrip:'pimpam',blockType:'product_single',products:['young'],includeHero:false,includeSteps:true,visible:true},
      {id:'block-012',title:'SmartJet FLEX Gama Completa',desc:'4 modelos del entry-level al industrial',priceRange:'desde 1.000 €/mes',colorTag:'teal',introText:'La gama SmartJet FLEX se adapta a distintos niveles de necesidad productiva, desde configuraciones de entrada hasta soluciones industriales con automatización, integración con ERP y e-commerce, y analítica en tiempo real. Todas producidas en Italia con cabezal HP PageWide:',brandStrip:'smartjet',blockType:'product_pair',products:['flexone','flex297'],includeHero:false,includeSteps:false,visible:true},
      {id:'block-013',title:'SmartJet FLEX Avanzadas',desc:'ULTRA + 324 para alta producción',priceRange:'Consultar',colorTag:'teal',introText:'Para entornos que requieren máxima productividad, control avanzado y velocidad industrial:',brandStrip:'smartjet',blockType:'product_pair',products:['flexultra','flex324'],includeHero:false,includeSteps:false,visible:true},
    ],
    prewrittenTexts: [
      {id:'text-001',name:'Intro UV-LED',icon:'💡',brand:'mix',text:'Hola,\n\nTras nuestra conversación, te envío información sobre algunas de nuestras impresoras UV-LED que creo que encajan con lo que necesitas.\n\nLa tecnología UV-LED permite imprimir directamente sobre casi cualquier material (plástico, madera, metal, vidrio, cerámica…) con secado instantáneo y calidad fotorrealista en CMYK + blanco.',visible:true,i18n:{fr:{text:"Bonjour,\n\nSuite à notre échange, je vous envoie les informations sur quelques-unes de nos imprimantes UV-LED qui correspondent à vos besoins.\n\nLa technologie UV-LED permet d'imprimer directement sur presque tous les matériaux (plastique, bois, métal, verre, céramique…) avec un séchage instantané et une qualité photoréaliste en CMYK + blanc."},de:{text:'Hallo,\n\nnach unserem Gespräch sende ich Ihnen Informationen zu einigen unserer UV-LED-Drucker, die gut zu Ihren Anforderungen passen.\n\nMit UV-LED-Technologie drucken Sie direkt auf nahezu jedes Material (Kunststoff, Holz, Metall, Glas, Keramik…) — mit sofortiger Trocknung und fotorealistischer Qualität in CMYK + Weiß.'},en:{text:"Hi,\n\nFollowing our conversation, here's some information on a few of our UV-LED printers that I think match what you're looking for.\n\nUV-LED technology lets you print directly onto almost any material (plastic, wood, metal, glass, ceramics…) with instant curing and photorealistic quality in CMYK + white."},nl:{text:'Hallo,\n\nNaar aanleiding van ons gesprek stuur ik u informatie over een aantal van onze UV-LED-printers die goed bij uw wensen passen.\n\nUV-LED-technologie maakt het mogelijk om rechtstreeks op vrijwel elk materiaal te printen (kunststof, hout, metaal, glas, keramiek…) met directe droging en fotorealistische kwaliteit in CMYK + wit.'}}},
      {id:'text-002',name:'Transición a MBO',icon:'▶',brand:'mbo',text:'En nuestra línea MBO (marca propia, excelente relación calidad-precio) también tenemos opciones interesantes:',visible:true,i18n:{fr:{text:'Dans notre gamme MBO (marque propre, excellent rapport qualité-prix), nous avons également des options intéressantes :'},de:{text:'In unserer MBO-Reihe (Eigenmarke, hervorragendes Preis-Leistungs-Verhältnis) haben wir ebenfalls interessante Optionen:'},en:{text:'In our MBO range (own brand, excellent value for money), we also have some interesting options:'},nl:{text:'In ons MBO-assortiment (eigen merk, uitstekende prijs-kwaliteitverhouding) hebben we ook interessante opties:'}}},
      {id:'text-003',name:'Transición a PimPam',icon:'🔹',brand:'pimpam',text:'Y si te interesa el concepto de vending con personalización automática, tenemos PimPam — máquinas que imprimen fundas de móvil al momento, sin operario:',visible:true,i18n:{fr:{text:'Et si le concept de vending avec personnalisation automatique vous intéresse, nous avons PimPam — des machines qui impriment des coques de téléphone à la demande, sans opérateur :'},de:{text:'Falls Sie das Konzept von Vending mit automatischer Personalisierung interessiert: Wir haben PimPam — Automaten, die Handyhüllen sofort bedrucken, ganz ohne Personal:'},en:{text:"And if you're interested in the concept of vending with automatic customization, we have PimPam — machines that print phone cases on the spot, with no operator needed:"},nl:{text:'En als u geïnteresseerd bent in vending met automatische personalisatie, dan hebben we PimPam — machines die telefoonhoesjes ter plekke bedrukken, zonder personeel:'}}},
      {id:'text-004',name:'Mención Freebird',icon:'✨',brand:'artisjet',text:'Por cierto, varios de estos equipos incluyen tecnología Freebird — un sensor láser que ajusta automáticamente la altura del cabezal para imprimir sobre objetos 3D irregulares:',visible:true,i18n:{fr:{text:"À noter : plusieurs de ces machines intègrent la technologie Freebird — un capteur laser qui ajuste automatiquement la hauteur de la tête d'impression pour imprimer sur des objets 3D irréguliers :"},de:{text:'Übrigens: Mehrere dieser Geräte verfügen über die Freebird-Technologie — ein Lasersensor, der die Druckkopfhöhe automatisch anpasst, um auf unregelmäßigen 3D-Objekten zu drucken:'},en:{text:'By the way, several of these machines include Freebird technology — a laser sensor that automatically adjusts the printhead height to print on irregular 3D objects:'},nl:{text:'Overigens: meerdere van deze machines beschikken over Freebird-technologie — een lasersensor die automatisch de printkophoogte aanpast om op onregelmatige 3D-objecten te printen:'}}},
      {id:'text-005',name:'Cierre comercial',icon:'👋',brand:'mix',text:'Si quieres, podemos organizar una demo (presencial o videollamada) para que veas cualquiera de estos equipos en funcionamiento. También te puedo enviar muestras impresas sin compromiso.\n\n¡Quedo a tu disposición!\nUn saludo,',visible:true,i18n:{fr:{text:"Si vous le souhaitez, nous pouvons organiser une démonstration (sur place ou en visioconférence) pour voir ces machines en action. Je peux également vous envoyer des échantillons imprimés sans engagement.\n\nÀ votre disposition !\nCordialement,"},de:{text:'Gerne können wir eine Demo organisieren (vor Ort oder per Videocall), damit Sie die Geräte in Aktion sehen. Ich kann Ihnen auch gerne gedruckte Muster unverbindlich zusenden.\n\nIch stehe Ihnen jederzeit zur Verfügung!\nMit freundlichen Grüßen,'},en:{text:"If you'd like, we can arrange a demo (on-site or video call) so you can see any of these machines in action. I can also send you printed samples with no obligation.\n\nLooking forward to hearing from you!\nBest regards,"},nl:{text:'Als u wilt, kunnen we een demo organiseren (op locatie of via videocall) zodat u deze machines in actie kunt zien. Ik kan u ook vrijblijvend gedrukte samples sturen.\n\nIk sta tot uw beschikking!\nMet vriendelijke groet,'}}},
      {id:'text-006',name:'Intro SmartJet',icon:'📦',brand:'smartjet',text:'Hola,\n\nNos complace informarte de que Bomedia es el distribuidor oficial de SmartJet para todo el territorio español.\n\nLa gama SmartJet FLEX es una línea de impresoras single-pass desarrollada en Italia con tecnología HP PageWide, diseñada para imprimir directamente sobre envases y embalajes acabados: cajas, bolsas de papel, sobres, pizza boxes, posavasos, packaging ecológico…\n\nCon resolución de hasta 1600 ppp, velocidad de hasta 46 m/min y un coste operativo muy competitivo, es la alternativa perfecta para tiradas cortas y medias, personalización bajo demanda y campañas promocionales.',visible:true,i18n:{en:{text:'Hi,\n\nWe are pleased to inform you that Bomedia is the official SmartJet distributor for Spain.\n\nThe SmartJet FLEX range is a line of single-pass printers developed in Italy with HP PageWide technology, designed to print directly on finished packaging: boxes, paper bags, envelopes, pizza boxes, coasters, eco-friendly packaging…\n\nWith resolution up to 1600 dpi, speed up to 46 m/min and very competitive operating costs, it\'s the perfect alternative for short and medium runs, on-demand customization and promotional campaigns.'}}},
      {id:'text-007',name:'Cierre SmartJet FESPA',icon:'🎪',brand:'smartjet',text:'Te invitamos a ver una SmartJet FLEX en funcionamiento en nuestras instalaciones de Sant Boi (Barcelona), los días 19, 20 y 21 de mayo. Sin compromiso.\n\nAdemás, disponemos de condiciones especiales de financiación: desde 1.000 €/mes a 5 años.\n\nQuedamos a tu disposición.\nUn saludo,',visible:true,i18n:{en:{text:'We invite you to see a SmartJet FLEX in action at our facilities in Sant Boi (Barcelona), on May 19, 20 and 21. No commitment.\n\nWe also have special financing conditions: from €1,000/month over 5 years.\n\nLooking forward to hearing from you.\nBest regards,'}}},
    ],
    templates: [
      {id:'tpl-001',name:'Compactas UV-LED',colorClass:'blue',brand:'mix',desc:'Introducción + 2 productos compactos + cierre',blocks:['text-001','block-001','text-002','block-002','text-005'],visible:true},
      {id:'tpl-002',name:'PimPam Vending',colorClass:'orange',brand:'pimpam',desc:'Propuesta completa de máquinas vending',blocks:['text-003','block-009','block-011','text-005'],visible:true},
      {id:'tpl-003',name:'Formatos varios',colorClass:'purple',brand:'mix',desc:'Catálogo gran formato industrial',blocks:['text-001','block-004','text-004','text-002','block-005','text-005'],visible:true},
      {id:'tpl-004',name:'Bloques modulares',colorClass:'gray',brand:'mix',desc:'Todas las marcas con strip final',blocks:['text-001','block-001','block-002','block-003','block-004','block-005','block-006','block-007','block-008','block-009','block-010','block-011','text-005'],visible:true},
      {id:'tpl-005',name:'SmartJet FLEX FESPA',colorClass:'teal',brand:'smartjet',desc:'Propuesta SmartJet con demo Barcelona mayo 2026',blocks:['text-006','block-012','text-007'],visible:true},
    ],
    standaloneBlocks: [
      {id:'sb-001',title:'Brand artisJet',desc:'Logo + enlace artisJet',icon:'aJ',iconBg:'#dbeafe',brand:'artisjet',section:'marcas',blockType:'brand_strip',config:{brand:'artisjet'},visible:true},
      {id:'sb-002',title:'Brand MBO',desc:'Logo + enlace MBO',icon:'MBO',iconBg:'#ede9fe',brand:'mbo',section:'marcas',blockType:'brand_strip',config:{brand:'mbo'},visible:true},
      {id:'sb-003',title:'Brand PimPam',desc:'Logo + enlace PimPam',icon:'PP',iconBg:'#ffedd5',brand:'pimpam',section:'marcas',blockType:'brand_strip',config:{brand:'pimpam'},visible:true},
      {id:'sb-004',title:'Vídeo Freebird',desc:'Banner con vídeo YouTube',icon:'▶',iconBg:'#1e3a8a',iconColor:'#fff',brand:'artisjet',section:'otros',blockType:'video',config:{youtubeUrl:'https://www.youtube.com/watch?v=gp-x_jRBRcE',thumbnailOverride:''},visible:true},
      {id:'sb-005',title:'1 Producto',desc:'Individual con selector',icon:'📦',iconBg:'#dcfce7',brand:'mix',section:'otros',blockType:'product_single',config:{defaultProduct:'young'},visible:true},
      {id:'sb-006',title:'2 Productos',desc:'Par con selectores',icon:'📦📦',iconBg:'#dcfce7',brand:'mix',section:'otros',blockType:'product_pair',config:{defaultProduct1:'young',defaultProduct2:'3000pro'},visible:true},
      {id:'sb-007',title:'3 Productos',desc:'Trío con selectores',icon:'📦³',iconBg:'#dcfce7',brand:'mix',section:'otros',blockType:'product_trio',config:{defaultProduct1:'uv1612g',defaultProduct2:'uv1812',defaultProduct3:'uv2513'},visible:true},
      {id:'sb-008',title:'PimPam Hero',desc:'Banner vending',icon:'🖼',iconBg:'#ffedd5',brand:'pimpam',section:'heroes',blockType:'pimpam_hero',config:{heroImage:'https://pimpam-vending.com/wp-content/uploads/2026/01/ChatGPT-Image-22-ene-2026-16_17_36.png',heroTitle:'Personaliza, imprime y vende… sin operario',heroSubtitle:'Impresión UV-LED directa sobre fundas de móvil en autoservicio completo.',heroBullets:['Autoservicio 100% — sin personal','Pago con tarjeta, móvil o QR','Funda impresa en HD en 30 segundos','Compatible con +600 modelos de móvil'],heroImageLink:'',heroCtaText:'',heroCtaUrl:'',heroBgColor:'#fff'},visible:true},
      {id:'sb-009',title:'PimPam 4 Pasos',desc:'Proceso en 4 pasos',icon:'1234',iconBg:'#ffedd5',brand:'pimpam',section:'otros',blockType:'pimpam_steps',config:{steps:[{n:"1️⃣",t:"Elige diseño",s:"Pantalla táctil"},{n:"2️⃣",t:"Personaliza",s:"Texto, colores…"},{n:"3️⃣",t:"Paga",s:"Tarjeta / QR"},{n:"4️⃣",t:"¡Listo!",s:"Funda en 30s"}],stepsBgColor:'#fff7ed',stepsBorderColor:'#fed7aa'},visible:true},
      {id:'sb-010',title:'Brand SmartJet',desc:'Logo + enlace SmartJet',icon:'SJ',iconBg:'#ccfbf1',brand:'smartjet',section:'marcas',blockType:'brand_strip',config:{brand:'smartjet'},visible:true},
      {id:'sb-011',title:'SmartJet Hero',desc:'Banner packaging SmartJet FLEX',icon:'🖼',iconBg:'#ccfbf1',brand:'smartjet',section:'heroes',blockType:'pimpam_hero',config:{heroImage:'https://boprint.net/wp-content/uploads/2025/12/smartjet_prodotti-1.png',heroTitle:'Imprime directamente sobre el envase',heroSubtitle:'SmartJet FLEX — single-pass HP PageWide · Producida en Italia · Desde 1.000 €/mes',heroBullets:['Cajas, bolsas, sobres, pizza boxes, packaging eco','Resolución hasta 1600 ppp · Velocidad hasta 46 m/min','Financiación desde 1.000 €/mes a 5 años','Software INTEGRA: control, analítica, integración ERP'],heroImageLink:'https://boprint.net/categoria-producto/smartjet-flex/',heroCtaText:'',heroCtaUrl:'',heroCtaButtons:[{text:'Ver gama FLEX',url:'https://boprint.net/categoria-producto/smartjet-flex/',bg:'#0d9488',color:'#fff'},{text:'Solicitar demo',url:'mailto:info@bomedia.net?subject=SmartJet%20FLEX%20demo',bg:'#ffffff',color:'#0d9488'}],heroBgColor:'#f0fdfa',i18n:{en:{heroTitle:'Print directly on the package',heroSubtitle:'SmartJet FLEX — HP PageWide single-pass · Made in Italy · From €1,000/mo',heroBullets:['Boxes, bags, envelopes, pizza boxes, eco packaging','Resolution up to 1600 dpi · Speed up to 46 m/min','Financing from €1,000/mo over 5 years','INTEGRA software: control, analytics, ERP integration'],heroCtaButtons:[{text:'View FLEX range',url:'https://boprint.net/categoria-producto/smartjet-flex/',bg:'#0d9488',color:'#fff'},{text:'Request demo',url:'mailto:info@bomedia.net?subject=SmartJet%20FLEX%20demo',bg:'#ffffff',color:'#0d9488'}]}}},visible:true},
    ],
    users: [
      { id: 'admin', name: 'Admin', role: 'admin', passwordHash: 'a1bfe0bf4fa8f02f1969c64276b15f55e455b3dd9f50f11a22fb8c284a9c2f48', hiddenItems: {}, aiStyles: {} },
    ],
    /* Saved CTA library — reusable call-to-action cards that can be picked
       from the column inserter. Each entry is a self-contained CTA card
       (optional title / bullets / button). Empty by default; user creates
       their own via Backoffice → CTAs. */
    ctaBlocks: [],
    openaiKey: '',
  }
}

let blockIdCounter = 0
function createBlock(type) {
  const id = 'b' + (++blockIdCounter) + '-' + Date.now().toString(36)
  switch (type) {
    case "text": return { id, type, _sourceType:'manual', _overrides:{es:"Hola,\n\nEscribe aquí tu texto personalizado."} }
    case "brand_artisjet": return { id, type, brand:"artisjet" }
    case "brand_mbo": return { id, type, brand:"mbo" }
    case "brand_pimpam": return { id, type, brand:"pimpam" }
    case "brand_smartjet": return { id, type, brand:"smartjet" }
    case "brand_flux": return { id, type, brand:"flux" }
    case "product_single": return { id, type, product1:"young" }
    case "product_pair": return { id, type, product1:"young", product2:"3000pro" }
    case "product_trio": return { id, type, product1:"uv1612g", product2:"uv1812", product3:"uv2513" }
    case "freebird": return { id, type }
    case "pimpam_hero": return { id, type }
    case "pimpam_steps": return { id, type }
    /* Multi-column layout container. `columns` is an array of column objects;
       each column has its own `blocks` array. Width is split equally for now
       (50/50, 33/33/33…). Used to lay out things like "text + image" side
       by side or 3 product cards in a single row. */
    case "section_2col": return { id, type:"section", layout:"2col", columns:[{ blocks:[] }, { blocks:[] }] }
    case "section_3col": return { id, type:"section", layout:"3col", columns:[{ blocks:[] }, { blocks:[] }, { blocks:[] }] }
    /* Single image block. Render as <img> with optional link wrapper. */
    case "image": return { id, type:"image", src:"", alt:"", link:"", align:"center", widthPct:100 }
    /* Call-to-action card. Optional title / subtitle / bullets above the
       button so the CTA can stand on its own as a "feature highlight"
       block, not just a naked link. The defaults are conservative so the
       user sees a useful starting point. */
    case "cta": return {
      id, type:"cta",
      title:"", subtitle:"", bullets:[],
      text:"Más información", url:"",
      bg:"#1d4ed8", color:"#ffffff", align:"center",
      panelBg:"transparent", panelBorder:"transparent",
    }
    /* Divisor visual entre bloques. Tres variantes: línea fina full
       width, línea corta centrada (más elegante para separar secciones)
       y puntos centrados (separador ornamental). */
    case "divider_line": return { id, type:"divider", style:"line", color:"#e2e8f0", paddingV:24 }
    case "divider_short": return { id, type:"divider", style:"short", color:"#cbd5e1", paddingV:32 }
    case "divider_dots": return { id, type:"divider", style:"dots", color:"#94a3b8", paddingV:28 }
    default: return { id, type }
  }
}

/* v3-compat globals: override the previous mock globals with v2 data.
   Each standaloneBlock exposes its v2 blockType under `.type` so the UI can
   dispatch on v2-native types (product_pair, pimpam_hero, brand_strip, etc.) */
const _DEFAULT_STATE = getDefaultState()
const PRODUCTS = _DEFAULT_STATE.products
const BRANDS = _DEFAULT_STATE.brands
const PREWRITTEN_TEXTS = _DEFAULT_STATE.prewrittenTexts
const TEMPLATES = _DEFAULT_STATE.templates
const COMPOSED_BLOCKS = _DEFAULT_STATE.composedBlocks
const STANDALONE_BLOCKS = _DEFAULT_STATE.standaloneBlocks.map(sb => Object.assign({}, sb, {
  type: sb.blockType,
}))

/* Re-poblar i18n.{lang}.link en productos cuando se haya "perdido" en
   Supabase (típicamente porque al editar un producto desde BO se sobre-
   escribió el campo i18n con el link base por error). La regla:
   - Si data.product.i18n[lang].link no existe → poner el de defaults
   - Si data.product.i18n[lang].link === data.product.link → "perdido",
     poner el de defaults (eran iguales al base, signo de pérdida)
   - Si tiene un valor distinto al base → respetar (es customización
     intencionada del usuario)
   Idempotente. Si los datos ya están bien, no hace nada. */
function repairProductLinks(state) {
  if (!state || typeof state !== 'object') return state
  if (!Array.isArray(state.products)) return state
  const defaults = getDefaultState()
  // Normalizamos los IDs para emparejar variantes con/sin guión: el data
  // del user puede tener "flex-one" mientras los defaults tienen "flexone".
  // La normalización quita guiones, guiones-bajos y bajamos a minúsculas.
  const normalizeId = (id) => String(id || '').toLowerCase().replace(/[-_]/g, '')
  const defaultMap = {}
  defaults.products.forEach(dp => {
    defaultMap[dp.id] = dp
    defaultMap[normalizeId(dp.id)] = dp
  })
  const langs = ['fr','de','en','nl']
  let touched = 0
  const next = state.products.map(p => {
    const dp = defaultMap[p.id] || defaultMap[normalizeId(p.id)]
    if (!dp || !dp.i18n) return p
    const baseLink = p.link || ''
    let i18n = p.i18n ? Object.assign({}, p.i18n) : {}
    let changedThis = false
    langs.forEach(lang => {
      const expected = dp.i18n[lang] && dp.i18n[lang].link
      if (!expected) return // defaults no tiene traducción para ese idioma
      const userLangBlock = i18n[lang] || {}
      const userLink = userLangBlock.link
      // Heurísticas para detectar i18n roto/perdido:
      // 1. No existe → poner el de defaults
      // 2. Es igual al base (sin traducir realmente)
      // 3. URL "rara" — el dominio coincide con el de defaults pero el path
      //    no es el esperado (ej. dominio de.artisjet pero slug español
      //    /producto/ — eso es señal clara de pérdida en BO).
      let isLost = !userLink || userLink === baseLink
      if (!isLost && userLink && expected) {
        try {
          const userUrl = new URL(userLink)
          const expectedUrl = new URL(expected)
          if (userUrl.host === expectedUrl.host && userUrl.pathname !== expectedUrl.pathname) {
            // Caso típico: artisjet-printers.eu/producto/foo (mal) vs
            // artisjet-printers.eu/shop/foo (bien). Mismo host, distinto path.
            // Si el path del user contiene "/producto/" y el expected
            // contiene "/shop/" o "/product/", lo consideramos roto.
            const userIsSpanishSlug = /\/producto\//.test(userUrl.pathname)
            const expectedIsForeign = /\/(shop|product)\//.test(expectedUrl.pathname)
            if (userIsSpanishSlug && expectedIsForeign) isLost = true
          }
        } catch (e) {} // URL inválida → no es roto, puede ser un edge case
      }
      if (isLost && userLink !== expected) {
        i18n[lang] = Object.assign({}, userLangBlock, { link: expected })
        changedThis = true
      }
    })
    // También parchea el base.link si en defaults cambió (caso flex324
    // que dejó de existir como página individual)
    if (dp.link && p.link !== dp.link && p.link === 'https://boprint.net/producto/flex-324/') {
      const fixed = Object.assign({}, p, { link: dp.link, i18n })
      touched++
      return fixed
    }
    if (changedThis) {
      touched++
      return Object.assign({}, p, { i18n })
    }
    return p
  })
  if (touched === 0) return state
  return Object.assign({}, state, { products: next })
}

/* Re-tag any legacy `mbo`-branded item whose visible text mentions DTF over
   to the new `mbo_dtf` brand. Mutation-free; returns the patched state. The
   match is conservative: only the item's own name/title/desc/text fields are
   inspected (not arbitrary nested fields), and the rule is "contains DTF as
   a whole token" — so "DTFkit" matches but "WiDTH" doesn't. Idempotent. */
function migrateMboDtf(state) {
  if (!state || typeof state !== 'object') return state
  const re = /\bDTF\b/i
  const isDtf = (s) => typeof s === 'string' && re.test(s)
  const retag = (item, fields) => {
    if (!item || item.brand !== 'mbo') return item
    const hit = fields.some(f => isDtf(item[f]))
    if (!hit) return item
    return Object.assign({}, item, { brand: 'mbo_dtf' })
  }
  const patched = Object.assign({}, state)
  if (Array.isArray(state.products))
    patched.products = state.products.map(p => retag(p, ['name','desc']))
  if (Array.isArray(state.prewrittenTexts))
    patched.prewrittenTexts = state.prewrittenTexts.map(t => retag(t, ['name','text']))
  if (Array.isArray(state.composedBlocks))
    patched.composedBlocks = state.composedBlocks.map(c => {
      let next = retag(c, ['title','desc','introText'])
      // brandStrip is the brand id used for the rendered logo strip — flip
      // it too so the email actually shows the DTF link rather than UV-LED.
      if (next.brandStrip === 'mbo' && [next.title, next.desc, next.introText].some(isDtf)) {
        next = Object.assign({}, next, { brandStrip: 'mbo_dtf' })
      }
      return next
    })
  if (Array.isArray(state.templates))
    patched.templates = state.templates.map(t => retag(t, ['name','desc']))
  if (Array.isArray(state.standaloneBlocks))
    patched.standaloneBlocks = state.standaloneBlocks.map(b => {
      let next = retag(b, ['title','desc'])
      // For brand_strip standalones, also flip the embedded config.brand
      if (next.config && next.config.brand === 'mbo' && (isDtf(next.title) || isDtf(next.desc))) {
        next = Object.assign({}, next, { config: Object.assign({}, next.config, { brand: 'mbo_dtf' }) })
      }
      return next
    })
  return patched
}

/* Migrar el schema viejo de bloques compuestos (introText + brandStrip +
   blockType + products[] + includeHero/includeSteps) al nuevo: una lista
   plana `compositorBlocks` con cada pieza como un bloque v3 estándar. Eso
   permite que el editor del backoffice (y el "Desagrupar" del composer)
   trabajen con la misma estructura que las plantillas y secciones — todo
   se compone como una lista de bloques en lugar de campos rígidos. La
   migración es idempotente: si ya existe `compositorBlocks` se respeta.
   También se propaga `i18n.{lang}.introText` al `overridesByLang` del
   bloque de texto generado para no perder traducciones. */
function migrateComposedToCompositorBlocks(state) {
  if (!state || !Array.isArray(state.composedBlocks)) return state
  let touched = 0
  const next = state.composedBlocks.map(c => {
    if (Array.isArray(c.compositorBlocks) && c.compositorBlocks.length > 0) return c
    const cb = []
    if (c.introText) {
      const overridesByLang = { es: c.introText }
      if (c.i18n) {
        for (const lang of Object.keys(c.i18n)) {
          const tr = c.i18n[lang]
          if (tr && tr.introText) overridesByLang[lang] = tr.introText
        }
      }
      cb.push({ type: 'text', overridesByLang })
    }
    if (c.brandStrip && c.brandStrip !== 'none') {
      cb.push({ type: 'brand_strip', brand: c.brandStrip })
    }
    const prods = Array.isArray(c.products) ? c.products : []
    if (c.blockType === 'product_trio' && prods.length >= 3) {
      cb.push({ type: 'product_trio', product1: prods[0], product2: prods[1], product3: prods[2] })
    } else if (c.blockType === 'product_pair' && prods.length >= 2) {
      cb.push({ type: 'product_pair', product1: prods[0], product2: prods[1] })
    } else if (c.blockType === 'product_single' && prods.length >= 1) {
      cb.push({ type: 'product_single', product1: prods[0] })
    } else {
      for (const pid of prods) cb.push({ type: 'product_single', product1: pid })
    }
    // Nota: deliberadamente no migramos includeHero/includeSteps. Son
    // legacy y a partir de v5 se gestionan como bloques sueltos
    // independientes (se pueden añadir manualmente al compuesto si el
    // user lo desea).
    if (cb.length === 0) return c
    touched++
    return Object.assign({}, c, { compositorBlocks: cb })
  })
  if (touched === 0) return state
  return Object.assign({}, state, { composedBlocks: next })
}

/* Normalizar bloques de tipo divider_line/short/dots (literal) al shape
   canónico {type:'divider', style:'line/short/dots'} que reconocen el
   renderer (dividerBlockHtml), el canvas BlockCard y el inspector
   DividerBlockEditor. Antes el factory del BO (CompositorBlocksListEditor
   y addSectionChild) escribía el tipo literal — esos divisores se
   guardaban en compositorBlocks de plantillas/compuestos y al cargar la
   plantilla en el composer no se renderizaban en el canvas (solo en el
   preview, gracias al fallback que hicimos en el bridge). Esta migración
   recorre todos los compositorBlocks anidados y los reescribe in-place.
   Idempotente: si ya están en formato canónico no toca nada. */
function migrateDividerTypes(state) {
  if (!state || typeof state !== 'object') return state
  let touched = 0
  const normalize = (block) => {
    if (!block || typeof block !== 'object') return block
    let next = block
    // Reescribir el bloque actual si es un divider_* literal
    if (block.type === 'divider_line' || block.type === 'divider_short' || block.type === 'divider_dots') {
      const style = block.type === 'divider_short' ? 'short'
                  : block.type === 'divider_dots' ? 'dots'
                  : 'line'
      next = Object.assign({}, block, { type: 'divider', style: block.style || style })
      touched++
    }
    // Recursar por columns[].blocks (secciones) — independiente del tipo
    // del propio bloque: una sección puede tener divisores como hijos.
    if (Array.isArray(next.columns)) {
      const cols = next.columns.map(col => Array.isArray(col.blocks)
        ? Object.assign({}, col, { blocks: col.blocks.map(normalize) })
        : col)
      next = Object.assign({}, next, { columns: cols })
    }
    return next
  }
  const walkList = (list) => Array.isArray(list)
    ? list.map(item => {
        if (!item || typeof item !== 'object') return item
        let v = item
        if (Array.isArray(v.compositorBlocks)) {
          v = Object.assign({}, v, { compositorBlocks: v.compositorBlocks.map(normalize) })
        }
        return v
      })
    : list
  const patched = Object.assign({}, state)
  if (Array.isArray(state.templates)) patched.templates = walkList(state.templates)
  if (Array.isArray(state.composedBlocks)) patched.composedBlocks = walkList(state.composedBlocks)
  if (touched === 0) return state
  return patched
}

/* Comprueba si un estado dado es indistinguible de los defaults frescos.
   Usado por el auto-save como cinturón-de-seguridad final: si por algún
   motivo (bug en hydration, race condition, etc.) el state acaba siendo
   los defaults pelados, NUNCA pushar a Supabase — risk de pisar el
   catálogo de todos los usuarios. La comparación combina señales
   rápidas (longitudes, presencia de extras del usuario) con un check
   profundo (JSON.stringify de las colecciones más grandes). Si CUALQUIER
   item diverge → el state no es pristine y el save procede.
   Apr 2026 audit fix. */
function _isPristineDefaults(state) {
  if (!state) return true
  const defaults = getDefaultState()
  // 1) Señales de "el user ha hecho algo" — si cualquiera está, NO es pristine.
  if (Array.isArray(state.users) && state.users.length > 1) return false
  if (Array.isArray(state.uploadedImages) && state.uploadedImages.length > 0) return false
  if (Array.isArray(state.imageLibrary) && state.imageLibrary.length > 0) return false
  if (Array.isArray(state.activityLog) && state.activityLog.length > 0) return false
  if (Array.isArray(state.ctaBlocks) && state.ctaBlocks.length > 0) return false
  if (state.openaiKey) return false
  // 2) Longitudes — defaults tienen un count exacto; cualquier desvío = user editó
  const sameLen = (a, b) => (Array.isArray(a) ? a.length : 0) === (Array.isArray(b) ? b.length : 0)
  if (!sameLen(state.products, defaults.products)) return false
  if (!sameLen(state.brands, defaults.brands)) return false
  if (!sameLen(state.templates, defaults.templates)) return false
  if (!sameLen(state.composedBlocks, defaults.composedBlocks)) return false
  if (!sameLen(state.standaloneBlocks, defaults.standaloneBlocks)) return false
  if (!sameLen(state.prewrittenTexts, defaults.prewrittenTexts)) return false
  // 3) Comparación profunda de las colecciones grandes — si los counts cuadran
  //    pero algún item se editó, divergencia byte a byte. JSON.stringify es
  //    barato comparado con la consecuencia de pisar la nube.
  try {
    if (JSON.stringify(state.products) !== JSON.stringify(defaults.products)) return false
    if (JSON.stringify(state.brands) !== JSON.stringify(defaults.brands)) return false
    if (JSON.stringify(state.templates) !== JSON.stringify(defaults.templates)) return false
    if (JSON.stringify(state.composedBlocks) !== JSON.stringify(defaults.composedBlocks)) return false
    if (JSON.stringify(state.standaloneBlocks) !== JSON.stringify(defaults.standaloneBlocks)) return false
    if (JSON.stringify(state.prewrittenTexts) !== JSON.stringify(defaults.prewrittenTexts)) return false
  } catch (e) {
    // Si serializar falla por algún motivo, asumimos NO-pristine (save procede)
    // — preferimos un save innecesario a perder datos del user.
    return false
  }
  return true
}

Object.assign(window, {
  DEFAULT_PRODUCTS, getDefaultState, createBlock, LANGS, LANG_LABELS,
  PRODUCTS, BRANDS, PREWRITTEN_TEXTS, TEMPLATES, STANDALONE_BLOCKS, COMPOSED_BLOCKS,
  migrateMboDtf, repairProductLinks, migrateComposedToCompositorBlocks, migrateDividerTypes,
  isPristineDefaults: _isPristineDefaults,
})
