import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthApiService } from './auth-api.service';

export interface SwinStatus {
  active?: boolean;
  available?: boolean;
  reason?: string;
  action?: string;
  model_path?: string;
  checkpoint_path?: string;
  labels?: string[];
  threshold?: number;
  error?: string;
  [key: string]: any;
}

export interface SwinTrainResponse {
  ok?: boolean;
  job_id?: string;
  status?: string;
  message?: string;
  command?: string;
  output_dir?: string;
  [key: string]: any;
}

export interface SwinJob {
  id?: string;
  job_id?: string;
  status?: string;
  document_type?: string;
  label?: string;
  model_name?: string;
  created_at?: string;
  updated_at?: string;
  metrics?: any;
  error?: string;
  [key: string]: any;
}

export interface SwinJobsResponse {
  items?: SwinJob[];
  [key: string]: any;
}

export interface SwinPredictionResponse {
  available?: boolean;
  document_class?: string | null;
  document_type?: string | null;
  template_id?: string | null;
  confidence?: number;
  method?: string;
  accepted?: boolean;
  predictions?: Array<{ label?: string; class_name?: string; confidence?: number; score?: number }>;
  [key: string]: any;
}

export interface SwinTrainPayload {
  documentType: string;
  templateId?: string;
  datasetName?: string;
  modelName: string;
  epochs: number;
  batchSize: number;
  imageSize: number;
  learningRate: number;
  validationSplit: number;
  notes?: string;
  datasetFile?: File | null;
}

@Injectable({ providedIn: 'root' })
export class SwinApiService {
  private readonly baseUrl = environment.apiBaseUrl.replace(/\/$/, '');

  constructor(private http: HttpClient, private auth: AuthApiService) {}

  status(): Observable<SwinStatus> {
    return this.http.get<SwinStatus>(`${this.baseUrl}/swin/status`, { headers: this.headers() });
  }

  jobs(): Observable<SwinJobsResponse | SwinJob[]> {
    return this.http.get<SwinJobsResponse | SwinJob[]>(`${this.baseUrl}/swin/jobs`, { headers: this.headers() });
  }

  startTraining(payload: SwinTrainPayload): Observable<SwinTrainResponse> {
    const form = new FormData();
    form.append('document_type', payload.documentType.trim());
    form.append('template_id', payload.templateId?.trim() || '');
    form.append('dataset_name', payload.datasetName?.trim() || payload.documentType.trim());
    form.append('model_name', payload.modelName);
    form.append('epochs', String(payload.epochs));
    form.append('batch_size', String(payload.batchSize));
    form.append('image_size', String(payload.imageSize));
    form.append('learning_rate', String(payload.learningRate));
    form.append('validation_split', String(payload.validationSplit));
    form.append('notes', payload.notes?.trim() || '');

    if (payload.datasetFile) {
      form.append('dataset', payload.datasetFile, payload.datasetFile.name);
    }

    return this.http.post<SwinTrainResponse>(`${this.baseUrl}/swin/train`, form, { headers: this.headers() });
  }

  predict(file: File): Observable<SwinPredictionResponse> {
    const form = new FormData();
    form.append('file', file, file.name);
    return this.http.post<SwinPredictionResponse>(`${this.baseUrl}/swin/predict`, form, { headers: this.headers() });
  }

  private headers(): HttpHeaders {
    return this.auth.authHeaders();
  }
}
