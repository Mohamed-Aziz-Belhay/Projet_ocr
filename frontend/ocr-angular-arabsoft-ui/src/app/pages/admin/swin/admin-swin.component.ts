import { CommonModule } from '@angular/common';
import { Component, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AuthApiService } from '../../../services/auth-api.service';
import {
  SwinApiService,
  SwinJob,
  SwinPredictionResponse,
  SwinStatus,
  SwinTrainPayload,
  SwinTrainResponse,
} from '../../../services/swin-api.service';

@Component({
  selector: 'ocr-admin-swin',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './admin-swin.component.html',
  styleUrl: './admin-swin.component.css',
})
export class AdminSwinComponent implements OnInit {
  status = signal<SwinStatus | null>(null);
  jobs = signal<SwinJob[]>([]);
  trainResponse = signal<SwinTrainResponse | null>(null);
  prediction = signal<SwinPredictionResponse | null>(null);

  loadingStatus = signal(false);
  loadingJobs = signal(false);
  training = signal(false);
  predicting = signal(false);
  error = signal<string | null>(null);
  message = signal<string | null>(null);

  datasetFileName = signal<string>('');
  predictFileName = signal<string>('');
  datasetFile: File | null = null;
  predictFile: File | null = null;

  trainForm: SwinTrainPayload = {
    documentType: 'custom_document',
    templateId: '',
    datasetName: 'custom_document_dataset',
    modelName: 'swin_tiny_patch4_window7_224',
    epochs: 8,
    batchSize: 8,
    imageSize: 224,
    learningRate: 0.00003,
    validationSplit: 0.2,
    notes: '',
    datasetFile: null,
  };

  readonly expectedDatasetTree = `dataset.zip\n├── train\n│   ├── nouveau_doc\n│   │   ├── img_001.jpg\n│   │   └── img_002.jpg\n│   └── autre_classe\n└── val\n    ├── nouveau_doc\n    └── autre_classe`;

  constructor(private swinApi: SwinApiService, public auth: AuthApiService) {}

  ngOnInit(): void {
    this.refreshAll();
  }

  refreshAll(): void {
    this.loadStatus();
    this.loadJobs();
  }

  loadStatus(): void {
    this.loadingStatus.set(true);
    this.error.set(null);

    this.swinApi.status().subscribe({
      next: value => {
        this.status.set(value);
        this.loadingStatus.set(false);
      },
      error: err => {
        this.status.set(null);
        this.error.set(err?.error?.detail || err?.message || 'Statut Swin indisponible. Vérifiez que le backend expose GET /swin/status.');
        this.loadingStatus.set(false);
      },
    });
  }

  loadJobs(): void {
    this.loadingJobs.set(true);

    this.swinApi.jobs().subscribe({
      next: value => {
        const items = Array.isArray(value) ? value : value?.items || [];
        this.jobs.set(items);
        this.loadingJobs.set(false);
      },
      error: () => {
        this.jobs.set([]);
        this.loadingJobs.set(false);
      },
    });
  }

  onDatasetFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] || null;
    this.datasetFile = file;
    this.datasetFileName.set(file?.name || '');
    this.trainForm.datasetFile = file;
  }

  onPredictFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] || null;
    this.predictFile = file;
    this.predictFileName.set(file?.name || '');
  }

  startTraining(): void {
    this.error.set(null);
    this.message.set(null);
    this.trainResponse.set(null);

    if (!this.trainForm.documentType.trim()) {
      this.error.set('Le type de document est obligatoire.');
      return;
    }

    if (!this.datasetFile) {
      this.error.set('Ajoutez un fichier dataset .zip avant de lancer l’entraînement.');
      return;
    }

    this.training.set(true);
    this.trainForm.datasetFile = this.datasetFile;

    this.swinApi.startTraining(this.trainForm).subscribe({
      next: response => {
        this.trainResponse.set(response);
        this.message.set(response?.message || 'Entraînement Swin lancé.');
        this.training.set(false);
        this.loadStatus();
        this.loadJobs();
      },
      error: err => {
        this.error.set(err?.error?.detail || err?.message || 'Impossible de lancer l’entraînement Swin.');
        this.training.set(false);
      },
    });
  }

  testPrediction(): void {
    this.error.set(null);
    this.prediction.set(null);

    if (!this.predictFile) {
      this.error.set('Ajoutez une image de test avant la prédiction.');
      return;
    }

    this.predicting.set(true);

    this.swinApi.predict(this.predictFile).subscribe({
      next: response => {
        this.prediction.set(response);
        this.predicting.set(false);
      },
      error: err => {
        this.error.set(err?.error?.detail || err?.message || 'Prédiction Swin impossible.');
        this.predicting.set(false);
      },
    });
  }

  resetForm(): void {
    this.trainForm = {
      documentType: 'custom_document',
      templateId: '',
      datasetName: 'custom_document_dataset',
      modelName: 'swin_tiny_patch4_window7_224',
      epochs: 8,
      batchSize: 8,
      imageSize: 224,
      learningRate: 0.00003,
      validationSplit: 0.2,
      notes: '',
      datasetFile: null,
    };
    this.datasetFile = null;
    this.datasetFileName.set('');
    this.message.set('Formulaire réinitialisé.');
  }

  statusLabel(value: SwinStatus | null): string {
    if (!value) return 'inconnu';
    if (value.active || value.available) return 'actif';
    if (value.error) return 'erreur';
    return 'inactif';
  }

  statusClass(value: SwinStatus | null): string {
    if (!value) return 'muted';
    if (value.active || value.available) return 'success';
    if (value.error) return 'danger';
    return 'warning';
  }

  percent(value: any): string {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return `${(n <= 1 ? n * 100 : n).toFixed(1)}%`;
  }

  formatDate(value: any): string {
    if (!value) return '—';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
  }

  asJson(value: any): string {
    return JSON.stringify(value || {}, null, 2);
  }
}
