import { CommonModule } from '@angular/common';
import { Component, ElementRef, OnDestroy, OnInit, ViewChild, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { OcrApiService, TemplateSummary } from '../../services/ocr-api.service';

interface YamlExample {
  id: string;
  title: string;
  description: string;
  filename: string;
  content: string;
}

interface TemplateBuilderField {
  name: string;
  label: string;
  type: 'text' | 'number' | 'date' | 'amount' | 'email' | 'id' | 'custom';
  extraction_method: 'regex';
  required: boolean;
  output_key: string;
  patterns: string[];
}

interface FieldPaletteItem {
  type: TemplateBuilderField['type'];
  title: string;
  description: string;
  icon: string;
}

interface RoiBox {
  id: string;
  name: string;
  label: string;
  type: 'text' | 'number' | 'date' | 'amount' | 'id' | 'custom';
  required: boolean;
  output_key: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

@Component({
  selector: 'ocr-templates',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './templates.component.html',
  styleUrl: './templates.component.css',
})
export class TemplatesComponent implements OnInit, OnDestroy {
  @ViewChild('catalogList') catalogList?: ElementRef<HTMLDivElement>;
  @ViewChild('yamlCarousel') yamlCarousel?: ElementRef<HTMLDivElement>;
  @ViewChild('roiImage') roiImage?: ElementRef<HTMLImageElement>;

  templates = signal<TemplateSummary[]>([]);
  selectedId = '';
  search = '';
  status = '';
  error = false;
  content = JSON.stringify(this.emptyTemplate(), null, 2);

  // ─────────────────────────────────────────────────────────────
  // Template Builder drag & drop
  // ─────────────────────────────────────────────────────────────
  builderId = 'custom_template';
  builderName = 'Nouveau template OCR';
  builderDocumentType = 'custom';
  builderLanguage = 'fr';
  builderEngine = 'paddle';
  builderFields: TemplateBuilderField[] = [];
  draggedFieldType: TemplateBuilderField['type'] | null = null;
  dropActive = false;

  fieldPalette: FieldPaletteItem[] = [
    { type: 'text', title: 'Champ texte', description: 'Nom, prénom, raison sociale, adresse...', icon: '🔤' },
    { type: 'number', title: 'Nombre', description: 'Quantité, numéro, identifiant numérique...', icon: '🔢' },
    { type: 'date', title: 'Date', description: 'Date de naissance, date facture, date extrait...', icon: '📅' },
    { type: 'amount', title: 'Montant', description: 'Total TTC, TVA, prix, capital...', icon: '💰' },
    { type: 'id', title: 'Identifiant', description: 'CIN, matricule, registre, référence...', icon: '🪪' },
    { type: 'email', title: 'Email', description: 'Adresse email détectée dans le document...', icon: '✉️' },
    { type: 'custom', title: 'Champ personnalisé', description: 'Champ libre avec regex personnalisée...', icon: '⚙️' },
  ];

  // ─────────────────────────────────────────────────────────────
  // ROI Template Designer
  // ─────────────────────────────────────────────────────────────
  roiTemplateId = 'roi_template';
  roiTemplateName = 'Template ROI';
  roiDocumentType = 'custom';
  roiLanguage = 'fr';
  roiEngine = 'paddle';
  roiImageUrl: string | null = null;
  roiImageFileName = '';
  roiImageDropActive = false;
  roiBoxes: RoiBox[] = [];
  activeRoiId: string | null = null;
  drawing = false;
  drawStartX = 0;
  drawStartY = 0;
  previewBox: RoiBox | null = null;

  yamlExamples: YamlExample[] = [
    {
      id: 'invoice_tn',
      title: 'Facture tunisienne',
      description: 'Exemple YAML pour facture tunisienne / TTN.',
      filename: 'invoice_tn.yaml',
      content: `id: invoice_tn
name: "Facture Tunisienne"
version: "3.0"
document_type: invoice
doc_family: invoice
language: fr
language_hints:
  - fr
  - ar
preferred_engine: paddle
critical_fields:
  - invoice_number
  - invoice_date
  - total_ttc
fields:
  - name: invoice_number
    label: "Numéro de facture"
    extraction_method: regex
    required: true
    output_key: invoiceNumber
    patterns:
      - "(?:Facture\\\\s*N[°o]?\\\\s*)([A-Z0-9][A-Z0-9\\\\-/]{0,30})"
  - name: invoice_date
    label: "Date de facture"
    extraction_method: regex
    required: true
    output_key: invoiceDate
    patterns:
      - "(?:^|\\\\s)Date[:\\\\s]+(\\\\d{1,2}[\\\\/\\\\-.]\\\\d{1,2}[\\\\/\\\\-.]\\\\d{2,4})(?:\\\\s|$)"
  - name: total_ttc
    label: "Montant T.T.C."
    extraction_method: regex
    required: true
    output_key: totalTTC
    patterns:
      - "Total\\\\s+T\\\\.?T\\\\.?C\\\\.?\\\\s+([0-9]+(?:[.,][0-9]+)?)"
output_mapping:
  invoice_number: invoiceNumber
  invoice_date: invoiceDate
  total_ttc: totalTTC
`,
    },
    {
      id: 'registre_commerce_tn',
      title: 'Registre RNE moderne',
      description: 'Exemple YAML valide pour registre de commerce tunisien moderne.',
      filename: 'registre_commerce_tn.yaml',
      content: `id: registre_commerce_tn
name: "Registre de Commerce Tunisien"
version: "1.0"
document_type: registre_commerce
doc_family: registre_commerce
language: fr
language_hints:
  - fr
  - ar
preferred_engine: paddle
critical_fields:
  - date_extrait
  - identifiant_unique
  - raison_sociale
fields:
  - name: date_extrait
    label: "Date de l'extrait"
    extraction_method: regex
    required: true
    output_key: extractDate
    patterns:
      - "\\\\b(20\\\\d{2}[/\\\\-.]\\\\d{1,2}[/\\\\-.]\\\\d{1,2})\\\\b"
  - name: identifiant_unique
    label: "Identifiant unique"
    extraction_method: regex
    required: true
    output_key: uniqueIdentifier
    patterns:
      - "\\\\b([0-9]{5,12}[A-Z])\\\\b"
  - name: raison_sociale
    label: "Raison sociale"
    extraction_method: regex
    required: true
    output_key: companyName
    patterns:
      - "(?:Raison\\\\s+sociale|الاسم\\\\s+الاجتماعي)\\\\s*[:\\\\-]?\\\\s*(.+?)(?=\\\\s+(?:Capital|Adresse|$))"
output_mapping:
  date_extrait: extractDate
  identifiant_unique: uniqueIdentifier
  raison_sociale: companyName
`,
    },
    {
      id: 'registre_commerce_legacy_ar',
      title: 'Registre legacy arabe',
      description: 'Exemple YAML pour ancien registre arabe/français.',
      filename: 'registre_commerce_legacy_ar.yaml',
      content: `id: registre_commerce_legacy_ar
name: "Registre de Commerce Tunisien - Format legacy"
version: "1.1"
document_type: registre_commerce
doc_family: business_registry
language: ar
language_hints:
  - ar
  - fr
  - en
preferred_engine: paddle
critical_fields:
  - date_extrait
  - identifiant_unique
  - denomination_sociale
fields:
  - name: identifiant_unique
    label: "Identifiant unique"
    extraction_method: regex
    required: true
    output_key: uniqueIdentifier
    patterns:
      - "\\\\b([0-9]{5,12}[A-Z])\\\\b"
  - name: numero_registre
    label: "Numéro registre"
    extraction_method: regex
    required: false
    output_key: registrationNumber
    patterns:
      - "\\\\b(B[0-9]{6,12})\\\\b"
  - name: denomination_sociale
    label: "Dénomination sociale"
    extraction_method: regex
    required: true
    output_key: companyName
    patterns:
      - "\\\\b([A-Z][A-Z\\\\s&\\\\.]{5,90}(?:CORPORATION|COMPANY|SARL|SA))\\\\b"
output_mapping:
  identifiant_unique: uniqueIdentifier
  numero_registre: registrationNumber
  denomination_sociale: companyName
`,
    },
    {
      id: 'cin_tn',
      title: 'CIN tunisienne',
      description: 'Exemple minimal pour carte CIN tunisienne.',
      filename: 'cin_tn.yaml',
      content: `id: cin_tn
name: "Carte d'Identité Nationale Tunisienne"
version: "1.0"
document_type: cin_tn
doc_family: id_document
language_hints:
  - ar
  - fr
preferred_engine: easyocr
critical_fields:
  - id_number
fields:
  - name: numero_cin
    label: "Numéro CIN"
    extraction_method: regex
    required: true
    output_key: idNumber
    patterns:
      - "\\\\b([0-9]{8})\\\\b"
output_mapping:
  numero_cin: idNumber
`,
    },
  ];

  constructor(private api: OcrApiService) {}

  ngOnInit(): void {
    this.load();
  }

  ngOnDestroy(): void {
    if (this.roiImageUrl) {
      URL.revokeObjectURL(this.roiImageUrl);
    }
  }

  emptyTemplate() {
    return {
      id: 'custom_template',
      name: 'Custom Template',
      document_type: 'custom',
      language: 'auto',
      preferred_engine: 'paddle',
      fields: [
        {
          name: 'field_name',
          type: 'text',
          extraction_method: 'regex',
          required: true,
          output_key: 'fieldName',
          patterns: ['Label\\\\s*[:\\\\-]?\\\\s*(.+)'],
        },
      ],
      output_mapping: { field_name: 'fieldName' },
    };
  }

  filtered() {
    const q = this.search.trim().toLowerCase();
    return this.templates().filter(
      t =>
        !q ||
        String(t.id || '').toLowerCase().includes(q) ||
        String(t.name || '').toLowerCase().includes(q) ||
        String(t.document_type || '').toLowerCase().includes(q)
    );
  }

  scrollCatalog(d: 'up' | 'down') {
    this.catalogList?.nativeElement.scrollBy({ top: d === 'down' ? 260 : -260, behavior: 'smooth' });
  }

  scrollYamlCarousel(d: 'left' | 'right') {
    this.yamlCarousel?.nativeElement.scrollBy({ left: d === 'right' ? 360 : -360, behavior: 'smooth' });
  }

  scrollToEditor(): void {
    setTimeout(() => {
      document.getElementById('templateEditorPanel')?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      });
    }, 80);
  }

  load() {
    this.api.listTemplates().subscribe({
      next: d => {
        this.templates.set(d || []);
        this.setStatus(`${d?.length || 0} templates chargés.`, false);
      },
      error: e => this.setStatus(`Erreur chargement templates: ${e.message || e}`, true),
    });
  }

  open(id: string) {
    this.api.getTemplate(id).subscribe({
      next: d => {
        this.selectedId = id;
        this.content = JSON.stringify(d, null, 2);
        this.setStatus(`Template chargé: ${id}`, false);
      },
      error: e => this.setStatus(`Template introuvable: ${e.message || e}`, true),
    });
  }

  newTemplate() {
    this.selectedId = '';
    this.content = JSON.stringify(this.emptyTemplate(), null, 2);
    this.setStatus('Nouveau template prêt.', false);
  }

  format() {
    try {
      this.content = JSON.stringify(JSON.parse(this.content), null, 2);
      this.setStatus('JSON formaté.', false);
    } catch {
      this.setStatus('JSON invalide.', true);
    }
  }

  save() {
    try {
      const d = JSON.parse(this.content);
      if (!d.id) throw new Error('Champ id obligatoire');
      this.api.saveTemplate(d.id, d).subscribe({
        next: () => {
          this.selectedId = d.id;
          this.setStatus(`Sauvegardé: ${d.id}`, false);
          this.load();
        },
        error: e => this.setStatus(JSON.stringify(e.error || e.message || e), true),
      });
    } catch (e: any) {
      this.setStatus(e.message || String(e), true);
    }
  }

  remove() {
    const id = this.selectedId || this.safeId();
    if (!id) {
      this.setStatus('Aucun template sélectionné.', true);
      return;
    }
    if (!confirm(`Supprimer ${id} ?`)) return;
    this.api.deleteTemplate(id).subscribe({
      next: () => {
        this.newTemplate();
        this.load();
      },
      error: e => this.setStatus(JSON.stringify(e.error || e.message || e), true),
    });
  }

  safeId() {
    try {
      return JSON.parse(this.content).id || '';
    } catch {
      return '';
    }
  }

  copy() {
    navigator.clipboard.writeText(this.content);
    this.setStatus('Contenu copié.', false);
  }

  useYamlExample(ex: YamlExample) {
    this.selectedId = ex.id;
    this.content = ex.content;
    this.setStatus(`Exemple chargé dans l'éditeur : ${ex.filename}`, false);
  }

  downloadYamlExample(ex: YamlExample) {
    this.downloadText(ex.content, ex.filename);
    this.setStatus(`Téléchargement lancé : ${ex.filename}`, false);
  }

  downloadAllYamlExamples() {
    const all = this.yamlExamples
      .map(e => `# =============================\n# ${e.filename}\n# =============================\n${e.content}`)
      .join('\n\n');
    this.downloadText(all, 'ocr_yaml_examples.yaml');
    this.setStatus('Téléchargement lancé : ocr_yaml_examples.yaml', false);
  }

  private downloadText(content: string, filename: string) {
    const blob = new Blob([content], { type: 'text/yaml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  setStatus(m: string, e: boolean) {
    this.status = m;
    this.error = e;
  }

  // ─────────────────────────────────────────────────────────────
  // Template Builder methods
  // ─────────────────────────────────────────────────────────────
  onPaletteDragStart(event: DragEvent, item: FieldPaletteItem): void {
    this.draggedFieldType = item.type;
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'copy';
      event.dataTransfer.setData('text/plain', item.type);
    }
  }

  onBuilderDragOver(event: DragEvent): void {
    event.preventDefault();
    this.dropActive = true;
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
  }

  onBuilderDragLeave(): void {
    this.dropActive = false;
  }

  onBuilderDrop(event: DragEvent): void {
    event.preventDefault();
    this.dropActive = false;
    const typeFromEvent = event.dataTransfer?.getData('text/plain') as TemplateBuilderField['type'];
    const type = this.draggedFieldType || typeFromEvent || 'custom';
    this.addBuilderField(type);
    this.draggedFieldType = null;
  }

  addBuilderField(type: TemplateBuilderField['type'] = 'custom'): void {
    const index = this.builderFields.length + 1;
    const name = this.defaultFieldName(type, index);
    const field: TemplateBuilderField = {
      name,
      label: this.defaultFieldLabel(type, index),
      type,
      extraction_method: 'regex',
      required: true,
      output_key: this.toCamelCase(name),
      patterns: [this.defaultPattern(type)],
    };
    this.builderFields.push(field);
    this.setStatus(`Champ ajouté : ${field.label}`, false);
  }

  removeBuilderField(index: number): void {
    this.builderFields.splice(index, 1);
    this.setStatus('Champ supprimé du builder.', false);
  }

  moveBuilderField(index: number, direction: 'up' | 'down'): void {
    const target = direction === 'up' ? index - 1 : index + 1;
    if (target < 0 || target >= this.builderFields.length) return;
    const current = this.builderFields[index];
    this.builderFields[index] = this.builderFields[target];
    this.builderFields[target] = current;
  }

  generateTemplateFromBuilder(): void {
    if (!this.builderId.trim()) {
      this.setStatus('ID du template obligatoire.', true);
      return;
    }
    if (!this.builderFields.length) {
      this.setStatus('Ajoutez au moins un champ dans le builder.', true);
      return;
    }

    const fields = this.builderFields.map(field => ({
      name: this.slugFieldName(field.name),
      label: field.label.trim(),
      type: field.type,
      extraction_method: field.extraction_method,
      required: field.required,
      output_key: field.output_key.trim() || this.toCamelCase(field.name),
      patterns: field.patterns.map(pattern => pattern.trim()).filter(pattern => !!pattern),
    }));

    const outputMapping: Record<string, string> = {};
    for (const field of fields) outputMapping[field.name] = field.output_key;

    const template = {
      id: this.slugFieldName(this.builderId.trim()),
      name: this.builderName.trim() || this.builderId.trim(),
      document_type: this.builderDocumentType.trim() || 'custom',
      language: this.builderLanguage.trim() || 'fr',
      preferred_engine: this.builderEngine || 'paddle',
      fields,
      output_mapping: outputMapping,
    };

    this.selectedId = template.id;
    this.content = JSON.stringify(template, null, 2);
    this.setStatus('Template généré depuis le builder. Vérifiez puis sauvegardez.', false);
    this.scrollToEditor();
  }

  loadBuilderFromJson(): void {
    try {
      const data = JSON.parse(this.content);
      this.builderId = data.id || 'custom_template';
      this.builderName = data.name || 'Nouveau template OCR';
      this.builderDocumentType = data.document_type || 'custom';
      this.builderLanguage = data.language || 'fr';
      this.builderEngine = data.preferred_engine || 'paddle';
      const fields = Array.isArray(data.fields) ? data.fields : [];
      this.builderFields = fields
        .filter((field: any) => field.extraction_method !== 'roi')
        .map((field: any, index: number) => ({
          name: field.name || `field_${index + 1}`,
          label: field.label || field.name || `Champ ${index + 1}`,
          type: field.type || 'custom',
          extraction_method: 'regex',
          required: field.required !== false,
          output_key: field.output_key || this.toCamelCase(field.name || `field_${index + 1}`),
          patterns: Array.isArray(field.patterns) && field.patterns.length ? field.patterns : [this.defaultPattern(field.type || 'custom')],
        }));
      this.setStatus('Builder rempli depuis le JSON actuel.', false);
    } catch {
      this.setStatus('Impossible de charger le builder : JSON invalide.', true);
    }
  }

  clearBuilder(): void {
    if (!confirm('Vider le Template Builder ?')) return;
    this.builderFields = [];
    this.builderId = 'custom_template';
    this.builderName = 'Nouveau template OCR';
    this.builderDocumentType = 'custom';
    this.builderLanguage = 'fr';
    this.builderEngine = 'paddle';
    this.setStatus('Template Builder réinitialisé.', false);
  }

  updateBuilderFieldName(index: number): void {
    const field = this.builderFields[index];
    if (!field) return;
    field.name = this.slugFieldName(field.name);
    field.output_key = field.output_key || this.toCamelCase(field.name);
  }

  addPattern(index: number): void {
    const field = this.builderFields[index];
    if (!field) return;
    field.patterns.push(this.defaultPattern(field.type));
  }

  removePattern(fieldIndex: number, patternIndex: number): void {
    const field = this.builderFields[fieldIndex];
    if (!field) return;
    field.patterns.splice(patternIndex, 1);
    if (!field.patterns.length) field.patterns.push(this.defaultPattern(field.type));
  }

  // ─────────────────────────────────────────────────────────────
  // ROI Designer methods
  // ─────────────────────────────────────────────────────────────
  onRoiImageSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] || null;
    if (!file) return;
    this.loadRoiImageFile(file);
    input.value = '';
  }

  onRoiImageDragOver(event: DragEvent): void {
    event.preventDefault();
    this.roiImageDropActive = true;
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
  }

  onRoiImageDragLeave(event?: DragEvent): void {
    event?.preventDefault();
    this.roiImageDropActive = false;
  }

  onRoiImageDrop(event: DragEvent): void {
    event.preventDefault();
    this.roiImageDropActive = false;
    const file = event.dataTransfer?.files?.[0] || null;
    if (!file) {
      this.setStatus('Aucun fichier image détecté.', true);
      return;
    }
    this.loadRoiImageFile(file);
  }

  private loadRoiImageFile(file: File): void {
    if (!file.type.startsWith('image/')) {
      this.setStatus('Le designer ROI accepte seulement des images.', true);
      return;
    }

    if (this.roiImageUrl) URL.revokeObjectURL(this.roiImageUrl);
    this.roiImageUrl = URL.createObjectURL(file);
    this.roiImageFileName = file.name;
    this.roiBoxes = [];
    this.previewBox = null;
    this.activeRoiId = null;
    this.setStatus(`Image chargée pour ROI : ${file.name}. Dessinez maintenant les zones sur l’image.`, false);
  }

  startRoiDraw(event: MouseEvent): void {
    if (!this.roiImageUrl) {
      this.setStatus('Chargez d’abord une image ROI.', true);
      return;
    }
    const point = this.getRoiPoint(event);
    this.drawing = true;
    this.drawStartX = point.x;
    this.drawStartY = point.y;
    this.previewBox = this.createRoiBox(point.x, point.y, 0, 0, this.roiBoxes.length + 1);
  }

  moveRoiDraw(event: MouseEvent): void {
    if (!this.drawing || !this.previewBox) return;
    const point = this.getRoiPoint(event);
    const x = Math.min(this.drawStartX, point.x);
    const y = Math.min(this.drawStartY, point.y);
    const width = Math.abs(point.x - this.drawStartX);
    const height = Math.abs(point.y - this.drawStartY);
    this.previewBox = { ...this.previewBox, x, y, width, height };
  }

  endRoiDraw(event: MouseEvent): void {
    if (!this.drawing || !this.previewBox) return;
    this.moveRoiDraw(event);
    const box = this.previewBox;
    this.drawing = false;
    this.previewBox = null;

    if (box.width < 2 || box.height < 2) {
      this.setStatus('Zone ROI trop petite.', true);
      return;
    }

    this.roiBoxes.push(box);
    this.activeRoiId = box.id;
    this.setStatus(`Zone ROI ajoutée : ${box.label}`, false);
  }

  selectRoiBox(id: string, event?: MouseEvent): void {
    if (event) event.stopPropagation();
    this.activeRoiId = id;
  }

  removeRoiBox(index: number): void {
    const removed = this.roiBoxes[index];
    this.roiBoxes.splice(index, 1);
    if (this.activeRoiId === removed?.id) this.activeRoiId = null;
    this.setStatus('Zone ROI supprimée.', false);
  }

  clearRoiDesigner(): void {
    if (!confirm('Vider le designer ROI ?')) return;
    this.roiBoxes = [];
    this.previewBox = null;
    this.activeRoiId = null;
    this.setStatus('Zones ROI réinitialisées.', false);
  }

  generateTemplateFromRoi(): void {
    if (!this.roiBoxes.length) {
      this.setStatus('Dessinez au moins une zone ROI.', true);
      return;
    }

    const fields = this.roiBoxes.map((box, index) => ({
      name: this.slugFieldName(box.name || `roi_field_${index + 1}`),
      label: box.label || `Zone ROI ${index + 1}`,
      type: box.type,
      extraction_method: 'roi',
      required: box.required,
      output_key: box.output_key || this.toCamelCase(box.name),
      roi: {
        x: this.round(box.x),
        y: this.round(box.y),
        width: this.round(box.width),
        height: this.round(box.height),
        unit: 'percent',
      },
    }));

    const outputMapping: Record<string, string> = {};
    for (const field of fields) outputMapping[field.name] = field.output_key;

    const template = {
      id: this.slugFieldName(this.roiTemplateId || 'roi_template'),
      name: this.roiTemplateName || 'Template ROI',
      document_type: this.roiDocumentType || 'custom',
      language: this.roiLanguage || 'fr',
      preferred_engine: this.roiEngine || 'paddle',
      template_mode: 'roi',
      sample_image: this.roiImageFileName || null,
      fields,
      output_mapping: outputMapping,
    };

    this.selectedId = template.id;
    this.content = JSON.stringify(template, null, 2);
    this.setStatus('Template ROI généré dans l’éditeur. Vérifiez puis sauvegardez.', false);
    this.scrollToEditor();
  }

  mergeRoiIntoCurrentJson(): void {
    if (!this.roiBoxes.length) {
      this.setStatus('Aucune zone ROI à fusionner.', true);
      return;
    }

    try {
      const data = JSON.parse(this.content || '{}');
      const existingFields = Array.isArray(data.fields) ? data.fields : [];
      const roiFields = this.roiBoxes.map((box, index) => ({
        name: this.slugFieldName(box.name || `roi_field_${index + 1}`),
        label: box.label || `Zone ROI ${index + 1}`,
        type: box.type,
        extraction_method: 'roi',
        required: box.required,
        output_key: box.output_key || this.toCamelCase(box.name),
        roi: {
          x: this.round(box.x),
          y: this.round(box.y),
          width: this.round(box.width),
          height: this.round(box.height),
          unit: 'percent',
        },
      }));

      data.fields = [...existingFields, ...roiFields];
      data.output_mapping = data.output_mapping || {};
      for (const field of roiFields) data.output_mapping[field.name] = field.output_key;
      this.content = JSON.stringify(data, null, 2);
      this.setStatus('Zones ROI fusionnées avec le JSON actuel.', false);
      this.scrollToEditor();
    } catch {
      this.setStatus('JSON actuel invalide. Impossible de fusionner les zones ROI.', true);
    }
  }

  private createRoiBox(x: number, y: number, width: number, height: number, index: number): RoiBox {
    const name = `roi_field_${index}`;
    return {
      id: `roi_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      name,
      label: `Zone ROI ${index}`,
      type: 'text',
      required: true,
      output_key: this.toCamelCase(name),
      x,
      y,
      width,
      height,
    };
  }

  private getRoiPoint(event: MouseEvent): { x: number; y: number } {
    const target = event.currentTarget as HTMLElement;
    const rect = target.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * 100;
    const y = ((event.clientY - rect.top) / rect.height) * 100;
    return {
      x: Math.max(0, Math.min(100, x)),
      y: Math.max(0, Math.min(100, y)),
    };
  }

  boxStyle(box: RoiBox) {
    return {
      left: `${box.x}%`,
      top: `${box.y}%`,
      width: `${box.width}%`,
      height: `${box.height}%`,
    };
  }

  activeRoiBox(): RoiBox | null {
    return this.roiBoxes.find(box => box.id === this.activeRoiId) || null;
  }

  updateRoiName(index: number): void {
    const box = this.roiBoxes[index];
    if (!box) return;
    box.name = this.slugFieldName(box.name);
    box.output_key = box.output_key || this.toCamelCase(box.name);
  }

  private round(value: number): number {
    return Math.round(value * 1000) / 1000;
  }

  // ─────────────────────────────────────────────────────────────
  // Shared helpers
  // ─────────────────────────────────────────────────────────────
  private defaultFieldName(type: TemplateBuilderField['type'], index: number): string {
    const map: Record<string, string> = {
      text: 'text_field',
      number: 'number_field',
      date: 'date_field',
      amount: 'amount_field',
      email: 'email_field',
      id: 'id_field',
      custom: 'custom_field',
    };
    return `${map[type] || 'field'}_${index}`;
  }

  private defaultFieldLabel(type: TemplateBuilderField['type'], index: number): string {
    const map: Record<string, string> = {
      text: 'Champ texte',
      number: 'Champ numérique',
      date: 'Date',
      amount: 'Montant',
      email: 'Email',
      id: 'Identifiant',
      custom: 'Champ personnalisé',
    };
    return `${map[type] || 'Champ'} ${index}`;
  }

  private defaultPattern(type: TemplateBuilderField['type']): string {
    switch (type) {
      case 'date':
        return '\\\\b(\\\\d{1,2}[\\\\/\\\\-.]\\\\d{1,2}[\\\\/\\\\-.]\\\\d{2,4})\\\\b';
      case 'amount':
        return '([0-9]+(?:[.,][0-9]{1,3})?)';
      case 'number':
        return '\\\\b([0-9]+)\\\\b';
      case 'id':
        return '\\\\b([A-Z0-9][A-Z0-9\\\\-/]{3,30})\\\\b';
      case 'email':
        return '([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\\\.[a-zA-Z]{2,})';
      case 'text':
        return 'Label\\\\s*[:\\\\-]?\\\\s*(.+)';
      default:
        return '(.+)';
    }
  }

  private slugFieldName(value: string): string {
    return (
      String(value || '')
        .trim()
        .toLowerCase()
        .replace(/[àáâä]/g, 'a')
        .replace(/[èéêë]/g, 'e')
        .replace(/[ìíîï]/g, 'i')
        .replace(/[òóôö]/g, 'o')
        .replace(/[ùúûü]/g, 'u')
        .replace(/ç/g, 'c')
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_+|_+$/g, '') || 'field_name'
    );
  }

  private toCamelCase(value: string): string {
    const clean = this.slugFieldName(value);
    return clean.replace(/_([a-z0-9])/g, (_, c) => String(c).toUpperCase());
  }
}
