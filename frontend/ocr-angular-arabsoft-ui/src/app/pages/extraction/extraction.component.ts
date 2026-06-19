import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { OcrApiService, TemplateSummary } from '../../services/ocr-api.service';
import { AuthApiService } from '../../services/auth-api.service';
import { ExportApiService } from '../../services/export-api.service';
import { Router } from '@angular/router';

type DocumentType =
  | 'auto'
  | 'passport'
  | 'id_document'
  | 'invoice'
  | 'cin_tn'
  | 'registre_commerce'
  | 'custom';

@Component({
  selector: 'extract',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './extraction.component.html',
  styleUrl: './extraction.component.css',
})
export class ExtractionComponent implements OnInit {
  templates = signal<TemplateSummary[]>([]);
  loading = signal(false);
  exportingPdf = signal(false);
  result = signal<any | null>(null);
  error = signal<string | null>(null);
  previewUrl = signal<string | null>(null);

  apiKey = localStorage.getItem('ocr_api_key') || 'dev-key-123';
  documentType: DocumentType = 'auto';
  templateId = '';
  languageHint = '';
  engine = 'auto';
  processingMode = 'balanced';
  cinMode = 'balanced';
  includeDiagnostics = true;
  selectedFile: File | null = null;

  templateOptions = computed(() => {
    const t = this.templates();

    return t.length
      ? [{ id: '', name: 'Auto' }, ...t]
      : [
          { id: '', name: 'Auto' },
          { id: 'invoice_tn', name: 'invoice_tn' },
          { id: 'cin_tn', name: 'cin_tn' },
          { id: 'registre_commerce', name: 'registre_commerce' },
          {
            id: 'registre_commerce_legacy_ar',
            name: 'registre_commerce_legacy_ar',
          },
          { id: 'passport_generic', name: 'passport_generic' },
        ];
  });

  constructor(
    private api: OcrApiService,
    private auth: AuthApiService,
    private exportApi: ExportApiService,
    private router: Router
  ) {}

  ngOnInit() {
    this.api.listTemplates().subscribe({
      next: d => this.templates.set(d || []),
      error: () => this.templates.set([]),
    });

    this.applyDefaults();
  }

  onTypeChange() {
    const map: any = {
      auto: '',
      passport: '',
      id_document: '',
      invoice: 'invoice_tn',
      cin_tn: 'cin_tn',
      registre_commerce: 'registre_commerce',
      custom: '',
    };

    this.templateId = map[this.documentType] || '';
    this.applyDefaults();
  }

  applyDefaults() {
    if (this.documentType === 'invoice') {
      this.engine = 'paddle';
      this.processingMode = 'balanced';
      this.languageHint = 'fr';
    } else if (this.documentType === 'passport') {
      this.engine = 'easyocr';
      this.processingMode = 'fast';
      this.languageHint = 'en';
    } else if (this.documentType === 'cin_tn') {
      this.engine = 'easyocr';
      this.processingMode = 'balanced';
      this.languageHint = 'ar';
    } else if (this.documentType === 'registre_commerce') {
      this.engine = 'paddle';
      this.processingMode = 'balanced';
      this.languageHint = 'fr';
    } else {
      this.engine = 'auto';
      this.processingMode = 'balanced';
      this.languageHint = '';
    }
  }

  

  getInvoiceLineItems(result: any, field?: any): any[] {
  const fromField = field?.value;

  if (Array.isArray(fromField)) {
    return fromField;
  }

  if (typeof fromField === 'string') {
    try {
      const parsed = JSON.parse(fromField);
      if (Array.isArray(parsed)) {
        return parsed;
      }
    } catch {
      // ignore
    }
  }

  const normalized = result?.normalized_data || result?.normalizedData || {};

  const candidates = [
    normalized.lineItems,
    normalized.line_items,
    normalized.items,
    normalized.invoiceLines,
    normalized.invoice_lines,
    result?.lineItems,
    result?.line_items,
    result?.diagnostics?.lineItems,
    result?.diagnostics?.line_items,
    result?.diagnostics?.invoice_line_items,
    result?.diagnostics?.raw_text_invoice_rules?.lineItems,
    result?.diagnostics?.raw_text_invoice_rules?.line_items,
  ];

  for (const candidate of candidates) {
    if (Array.isArray(candidate)) {
      return candidate;
    }

    if (typeof candidate === 'string') {
      try {
        const parsed = JSON.parse(candidate);
        if (Array.isArray(parsed)) {
          return parsed;
        }
      } catch {
        // ignore
      }
    }
  }

  return [];
}


  invoiceCell(item: any, keys: string[], fallback = '—'): string {
    if (!item || typeof item !== 'object') {
      return fallback;
    }

    for (const key of keys) {
      const value = item[key];

      if (value !== null && value !== undefined && value !== '') {
        return String(value);
      }
    }

    return fallback;
  }

  invoiceCode(item: any): string {
    return this.invoiceCell(item, [
      'code',
      'itemCode',
      'item_code',
      'reference',
      'ref',
      'article',
    ]);
  }

  invoiceDesignation(item: any): string {
    return this.invoiceCell(item, [
      'designation',
      'description',
      'label',
      'name',
      'libelle',
      'libellé',
    ]);
  }

  invoiceQuantity(item: any): string {
    return this.invoiceCell(item, [
      'quantity',
      'quantite',
      'quantité',
      'qty',
      'qte',
    ]);
  }

  invoiceUnitPrice(item: any): string {
    return this.invoiceCell(item, [
      'prix_unitaire_htva',
      'prix_unitaire_ht',
      'pu_ht',
      'puHT',
      'unit_price',
      'unitPrice',
      'unitPriceHT',
      'unit_ht',
      'unitHT',
    ]);
  }

  invoiceVat(item: any): string {
    return this.invoiceCell(item, [
      'taux_tva',
      'vat_rate',
      'vatRate',
      'tva',
      'vat',
      'tax',
    ]);
  }

  invoiceTotal(item: any): string {
    return this.invoiceCell(item, [
      'total_htva',
      'total_ht',
      'totalHT',
      'total_ttc',
      'totalTTC',
      'total',
      'line_total',
      'lineTotal',
      'amount',
    ]);
  }


  engineHelp() {
    const h: any = {
      invoice:
        'Paddle + balanced : OCR pleine page + règles métier facture tunisienne.',
      passport: 'EasyOCR + fast : lecture MRZ prioritaire.',
      cin_tn: 'EasyOCR + balanced : plus stable pour plusieurs champs arabes.',
      registre_commerce:
        'Paddle + balanced : extraction RNE/legacy par chemin spécialisé.',
      custom: 'Paddle pour documents imprimés ; EasyOCR pour photos/cartes.',
    };

    return (
      h[this.documentType] ||
      'Auto : utile pour tester le routage ; type explicite recommandé en démo.'
    );
  }

  onFileChange(e: Event) {
    const input = e.target as HTMLInputElement;
    const file = input.files?.[0] || null;

    this.selectedFile = file;
    this.previewUrl.set(null);

    if (file && file.type.startsWith('image/')) {
      this.previewUrl.set(URL.createObjectURL(file));
    }
  }

  submit() {
    this.error.set(null);
    
    if (!this.auth.isLoggedIn()) {
      this.error.set(
        "Vous devez vous connecter pour lancer une extraction. Créez un compte simple user ou demandez un accès operator."
      );
      return;
    }

    if (!this.auth.canExtract()) {
      this.error.set("Votre rôle ne permet pas de lancer une extraction.");
      return;
    }

this.result.set(null);

    if (!this.auth.isLoggedIn()) {
      this.error.set(
        "Vous devez vous connecter pour lancer une extraction. Créez un compte simple user ou demandez un accès operator."
      );
      return;
    }

    if (!this.auth.canExtract()) {
      this.error.set(
        "Votre rôle simple user ne permet pas de lancer une extraction. Vous devez demander un accès operator et attendre la validation de l'admin."
      );
      return;
    }

    if (!this.apiKey.trim()) {
      this.error.set('Clé API obligatoire.');
      return;
    }

    if (!this.selectedFile) {
      this.error.set('Sélectionne un fichier.');
      return;
    }

    localStorage.setItem('ocr_api_key', this.apiKey.trim());
    this.loading.set(true);

    this.api
      .extract({
        file: this.selectedFile,
        documentType: this.documentType,
        templateId: this.templateId || undefined,
        engine: this.engine,
        processingMode: this.processingMode,
        cinMode: this.cinMode,
        languageHint: this.languageHint || undefined,
        includeDiagnostics: this.includeDiagnostics,
      })
      .subscribe({
        next: d => {
          this.result.set(d);
          this.saveLastResult(d);
          this.loading.set(false);
        },
        error: e => {
          this.loading.set(false);

          const detail = e?.error?.detail;

          if (detail?.error === 'DOCUMENT_TYPE_MISMATCH') {
            const confidence =
              typeof detail.confidence === 'number'
                ? `${(detail.confidence * 100).toFixed(0)}%`
                : '—';

            const reasons = Array.isArray(detail.reasons)
              ? detail.reasons.join(', ')
              : '—';

            this.error.set(
              `${detail.message}\n\n` +
                `Type choisi : ${detail.selected_type}\n` +
                `Type détecté : ${detail.detected_type}\n` +
                `Confiance : ${confidence}\n` +
                `Indices : ${reasons}\n` +
                `Recommandation : ${detail.recommendation}`
            );

            return;
          }

          if (typeof detail === 'string') {
            this.error.set(detail);
            return;
          }

          if (detail?.message) {
            this.error.set(detail.message);
            return;
          }

          this.error.set(JSON.stringify(e.error || e.message || e, null, 2));
        },
      });
  }

  reset() {
    this.result.set(null);
    this.error.set(null);
    this.previewUrl.set(null);
    this.selectedFile = null;
    this.documentType = 'auto';
    this.templateId = '';
    this.applyDefaults();
  }

  statusLabel(s: string) {
    return (
      ({
        success: 'Succès',
        partial: 'Partiel',
        failed: 'Échec',
        review_required: 'Révision requise',
      } as any)[s] ||
      s ||
      '-'
    );
  }

  statusClass(s: string) {
    return (
      ({
        success: 'ok',
        partial: 'info',
        failed: 'danger',
        review_required: 'warn',
      } as any)[s] || 'info'
    );
  }

  percent(v: any) {
    const n = Number(v);
    return Number.isFinite(n) ? `${(n <= 1 ? n * 100 : n).toFixed(1)}%` : '—';
  }

  formatMs(v: any) {
    const n = Number(v);

    return Number.isFinite(n)
      ? n < 1000
        ? `${Math.round(n)} ms`
        : `${(n / 1000).toFixed(2)} s`
      : '—';
  }

  fieldLabel(n: string) {
    const l: any = {
      invoice_number: 'Numéro de facture',
      invoice_date: 'Date de facture',
      reference_unique: 'Référence unique',
      total_ht: 'Total H.T.',
      vat_amount: 'Montant TVA',
      vat_rate: 'Taux TVA',
      stamp_amount: 'Droit de timbre',
      total_ttc: 'Total T.T.C.',
      currency: 'Devise',
      amount_consistency: 'Cohérence des montants',
      line_items: 'Lignes de facture',
      date_extrait: "Date de l'extrait",
      identifiant_unique: 'Identifiant unique',
      raison_sociale: 'Raison sociale',
      numero_registre: 'Numéro registre',
      numero_depot: 'Numéro dépôt',
      numero_interne: 'Numéro interne',
      capital: 'Capital',
      date_publication: 'Date de publication',
      date_debut_activite: "Date début d'activité",
      dirigeant_date_naissance: 'Date naissance dirigeant',
    };

    return l[n] || String(n || '').replace(/_/g, ' ');
  }

  displayValue(v: any) {
    if (v === true) return 'Oui';
    if (v === false) return 'Non';
    if (v === null || v === undefined || v === '') return '—';
    if (typeof v === 'object') return JSON.stringify(v, null, 2);

    return String(v);
  }

  isLineItems(f: any) {
    return f?.name === 'line_items' || f?.name === 'lineItems';
  }

  lineItems(f: any) {
    return this.getInvoiceLineItems(this.result?.(), f);
  }

  copyJson() {
    navigator.clipboard.writeText(JSON.stringify(this.result(), null, 2));
  }

  downloadJson() {
    const d = this.result();
    if (!d) return;

    this.exportApi.downloadJson(d, this.exportFileName('json'));
  }

  downloadCsv() {
    const d = this.result();
    if (!d) return;

    this.exportApi.downloadCsv(d, this.exportFileName('csv'));
  }

  downloadPdf() {
    const d = this.result();
    if (!d) return;

    this.exportingPdf.set(true);

    this.exportApi.exportPdf(this.buildExportPayload(d)).subscribe({
      next: blob => {
        this.exportApi.downloadBlob(blob, this.exportFileName('pdf'));
        this.exportingPdf.set(false);
      },
      error: err => {
        this.error.set(
          JSON.stringify(
            err?.error || err?.message || 'Export PDF indisponible. Vérifie que /exports/pdf existe côté backend.',
            null,
            2
          )
        );
        this.exportingPdf.set(false);
      },
    });
  }

  openAssistant() {
    const d = this.result();

    if (d) {
      this.saveLastResult(d);
    }

    this.router.navigate(['/assistant']);
  }

  private saveLastResult(result: any): void {
    const payload = this.buildExportPayload(result);

    try {
      localStorage.setItem('ocr_last_result', JSON.stringify(payload));
    } catch {
      // localStorage may fail if the OCR payload is very large.
      const lightPayload = {
        ...payload,
        result: {
          status: result?.status,
          global_confidence: result?.global_confidence,
          processing_time_ms: result?.processing_time_ms,
          document_type: result?.document_type || this.documentType,
          template_id: result?.template_id || this.templateId,
          normalized_data: result?.normalized_data || result?.normalizedData || {},
          fields: Array.isArray(result?.fields) ? result.fields.slice(0, 30) : [],
        },
      };

      localStorage.setItem('ocr_last_result', JSON.stringify(lightPayload));
    }
  }

  private buildExportPayload(result: any) {
    return {
      file_name: this.selectedFile?.name || result?.file_name || 'document',
      document_type: result?.document_type || this.documentType,
      template_id: result?.template_id || this.templateId || null,
      result,
      metadata: {
        engine: this.engine,
        processing_mode: this.processingMode,
        cin_mode: this.cinMode,
        language_hint: this.languageHint,
        exported_at: new Date().toISOString(),
      },
    };
  }

  private exportFileName(ext: 'json' | 'csv' | 'pdf'): string {
    const base = (this.selectedFile?.name || this.result()?.file_name || this.documentType || 'document')
      .replace(/\.[^.]+$/, '')
      .replace(/[^a-zA-Z0-9_-]+/g, '_')
      .replace(/^_+|_+$/g, '');

    return `ocr_${base || 'document'}_${new Date().toISOString().slice(0, 10)}.${ext}`;
  }
}
