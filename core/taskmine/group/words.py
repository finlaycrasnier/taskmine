"""Fixed word lists of the noise, grouping and naming stages (English and Italian)."""

STOP_WORDS = (
    "the", "and", "for", "with", "from", "this", "that", "into", "your", "you", "are", "was",
    "were", "has", "have", "not", "but", "all", "any", "can", "our", "out", "new", "use", "using",
    "per", "via", "one", "two", "how", "what", "when", "who", "why", "which", "about", "after",
    "before", "over", "under", "then", "than", "them", "they", "their", "there", "here", "also",
    "just", "more", "most", "some", "such", "only", "own", "same", "very", "will", "would",
    "should", "could", "onto", "upon", "off", "its", "it's", "let", "lets", "get", "got",
    "please", "thanks", "thank", "dear", "hello", "regards",
)

MENU_WORDS = (
    "file", "edit", "view", "insert", "format", "tools", "window", "help", "share", "more",
    "settings", "search", "back", "forward", "next", "previous", "close", "open", "save", "print",
    "undo", "redo", "cut", "copy", "paste", "select", "all", "find", "replace", "zoom", "bold",
    "italic", "underline", "strikethrough", "font", "size", "style", "regular", "typeface",
    "helvetica", "align", "left", "right", "centre", "center", "fully", "justify", "list", "line",
    "spacing", "colour", "color", "background", "text", "document", "actions", "sidebar",
    "toolbar", "ruler", "new", "reply", "archive", "delete", "move", "filter", "sort", "refresh",
    "sign", "account", "apps", "menu", "options", "tab", "bookmarks", "history", "downloads",
    "extensions", "profile", "notifications", "inbox", "sent", "drafts", "trash", "spam",
    "outbox", "junk", "rgb",
    "modifica", "vista", "inserisci", "formato", "strumenti", "finestra", "aiuto", "condividi",
    "altro", "impostazioni", "cerca", "indietro", "avanti", "successivo", "precedente", "chiudi",
    "apri", "salva", "stampa", "annulla", "ripeti", "taglia", "copia", "incolla", "seleziona",
    "tutto", "trova", "sostituisci", "grassetto", "corsivo", "sottolineato", "barrato",
    "carattere", "dimensione", "stile", "allinea", "sinistra", "destra", "centro", "giustifica",
    "elenco", "interlinea", "colore", "sfondo", "testo", "documento", "azioni", "nuovo",
    "rispondi", "inoltra", "archivia", "elimina", "sposta", "filtra", "ordina", "aggiorna",
    "accedi", "opzioni", "scheda", "segnalibri", "cronologia", "estensioni", "profilo",
    "notifiche", "posta", "arrivo", "inviata", "bozze", "cestino",
)

WARNING_WORDS = (
    "error", "failed", "rejected", "declined", "denied", "invalid", "overdue", "past due",
    "expired", "warning", "unable", "cannot", "could not", "not found", "missing", "blocked",
    "errore", "fallito", "non riuscito", "rifiutato", "respinto", "negato", "non valido",
    "scaduto", "avviso", "impossibile", "non trovato", "mancante", "bloccato",
)

STATUS_WORDS = (
    "saved", "sent", "submitted", "approved", "completed", "posted", "paid", "pending",
    "in review", "awaiting", "on hold", "uploaded", "synced", "status",
    "salvato", "inviato", "approvato", "completato", "pagato", "in attesa", "in revisione",
    "caricato", "sincronizzato", "stato",
)

FILE_KINDS = (
    "pdf", "xls", "xlsx", "xlsm", "doc", "docx", "csv", "txt", "rtf", "ppt", "pptx", "png", "jpg",
    "jpeg", "eml", "msg", "xml", "json", "zip", "numbers", "pages",
)

VERBS = ("enter", "check", "approve", "reconcile", "reply", "update", "create", "review", "file")

OBJECTS = (
    "invoice", "purchase order", "ticket", "customer", "supplier", "employee record", "timesheet",
    "schedule", "payment", "expense claim", "contract", "order", "report", "email", "document",
)

CASE_PREFIX_OBJECTS = (
    ("INV", "invoice"), ("FAT", "invoice"), ("FT", "invoice"), ("PO", "purchase order"),
    ("ODA", "purchase order"), ("SUP", "ticket"), ("TCK", "ticket"), ("TKT", "ticket"),
    ("TIC", "ticket"), ("INC", "ticket"), ("REQ", "ticket"), ("HD", "ticket"), ("SR", "ticket"),
    ("CASE", "ticket"), ("ORD", "order"), ("SO", "order"), ("EMP", "employee record"),
    ("HR", "employee record"), ("EXP", "expense claim"), ("TS", "timesheet"), ("PAY", "payment"),
    ("CTR", "contract"), ("CON", "contract"), ("CUS", "customer"), ("CUST", "customer"),
    ("VEN", "supplier"), ("RPT", "report"),
)

OBJECT_PATTERNS = (
    (r"purchase orders?|ordin[ei] d'acquisto", "purchase order"),
    (r"invoices?|fattur[ae]", "invoice"),
    (r"tickets?|support requests?|segnalazion[ei]", "ticket"),
    (r"timesheets?|fogli? (?:ore|presenze)", "timesheet"),
    (r"rotas?|shift plans?|schedules?|turni", "schedule"),
    (r"expense claims?|expenses|not[ae] spese|rimbors[oi]", "expense claim"),
    (r"payments?|pagament[oi]|bonific[oi]|remittances?", "payment"),
    (r"contracts?|contratt[oi]", "contract"),
    (r"employees?|dipendent[ei]|payroll|buste? paga", "employee record"),
    (r"customers?|client[ei]", "customer"),
    (r"suppliers?|vendors?|fornitor[ei]", "supplier"),
    (r"sales orders?|orders?|ordin[ei]", "order"),
    (r"reports?|rapport[oi]", "report"),
)

APPROVE_CLICKS = ("approve", "approva", "approvare", "authorise", "authorize", "autorizza",
                  "sign off", "accept")
REPLY_CLICKS = ("reply", "rispondi", "respond", "answer")
FILE_CLICKS = ("attach", "upload", "allega", "carica", "move to", "archive", "archivia")
APPROVAL_LINE = (r"^(?:(?:status|stato|state|esito|decision|decisione)\s*:\s*)?"
                 r"(?:approved|approvat[oa]|autorizzat[oa]|approval granted|approvazione concessa)\b")
REPLY_LINE = (r"^(?:reply|re|risposta|answer)\s*:|^(?:dear|hi|hello|gentile|buongiorno|salve)\b"
              r"|thank you for|thanks for|grazie per")
RECONCILE = r"reconcil|riconcili|bank statements?|estratt[oi] conto"
REPLY_OBJECTS = ("ticket", "email", "customer", "supplier")

SUMMARY_WORDS = (
    ("create", "created a document"), ("view", "read"), ("search", "searched"),
    ("browse", "opened pages"), ("copy", "copied details"), ("type", "typed"), ("paste", "pasted"),
    ("save", "saved"), ("send", "sent"), ("click", "used controls"),
)

FIELD_WORDS = (
    "supplier", "customer", "client", "vendor", "amount", "total", "subtotal", "net", "gross",
    "vat", "tax", "due", "date", "status", "invoice", "number", "order", "purchase", "reference",
    "ref", "description", "quantity", "qty", "price", "unit", "problem", "issue", "reply",
    "subject", "account", "iban", "bank", "payment", "method", "terms", "currency", "notes",
    "note", "comment", "comments", "priority", "category", "type", "owner", "assignee",
    "employee", "department", "project", "cost", "centre", "center", "code", "period", "hours",
    "start", "end", "from", "to", "cc", "contact", "address", "email", "phone", "name", "company",
    "item", "items", "delivery", "shipping", "approval", "approved", "approver", "requested",
    "request", "title", "summary", "result", "sku", "document", "file", "attachment", "balance",
    "statement", "ticket",
    "fornitore", "cliente", "importo", "totale", "imponibile", "iva", "scadenza", "data", "stato",
    "fattura", "numero", "ordine", "riferimento", "descrizione", "quantità", "prezzo", "problema",
    "risposta", "oggetto", "conto", "banca", "pagamento", "metodo", "valuta", "note", "nota",
    "commento", "priorità", "categoria", "tipo", "dipendente", "reparto", "progetto", "costo",
    "centro", "codice", "periodo", "ore", "inizio", "fine", "contatto", "indirizzo", "telefono",
    "nome", "azienda", "articolo", "consegna", "approvazione", "richiesta", "titolo", "documento",
    "allegato", "saldo",
)

CONNECTORS = ("a", "an", "the", "to", "in", "on", "at", "of", "for", "from", "and", "or", "with",
              "about", "into", "by", "per")

SITE_PREFIXES = ("www", "app", "go", "my", "secure", "portal", "login", "web")

PERSONAL_SITES = (
    ("video", ("youtube.com", "youtu.be", "netflix.com", "twitch.tv", "primevideo.com",
               "disneyplus.com", "raiplay.it", "dazn.com")),
    ("music", ("spotify.com", "soundcloud.com", "deezer.com", "music.apple.com")),
    ("social", ("facebook.com", "instagram.com", "tiktok.com", "pinterest.com", "snapchat.com")),
    ("shopping", ("amazon.*", "ebay.*", "zalando.*", "etsy.com", "aliexpress.com", "vinted.*",
                  "subito.it")),
    ("games", ("steampowered.com", "roblox.com", "epicgames.com", "chess.com")),
)

MAIL_APPS = ("com.apple.mail", "com.microsoft.outlook")
MAIL_HOSTS = ("mail.google.com", "outlook.office.com", "outlook.live.com", "outlook.office365.com")
