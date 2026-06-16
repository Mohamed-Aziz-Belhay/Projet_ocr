import { CommonModule } from '@angular/common';
import { Component, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AssistantApiService } from '../../services/assistant-api.service';
import { AuthApiService } from '../../services/auth-api.service';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  suggestions?: string[];
  severity?: string;
  mode?: string;
  debug?: any;
}

@Component({
  selector: 'ocr-assistant',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './ocr-assistant.component.html',
  styleUrl: './ocr-assistant.component.css',
})
export class OcrAssistantComponent {
  message = '';
  useAi = false;
  includeLastResult = true;
  includeHistory = true;
  showDebug = false;

  loading = signal(false);
  checkingStatus = signal(false);
  error = signal<string | null>(null);
  hasLastResult = signal(false);
  assistantStatus = signal<any | null>(null);

  messages = signal<ChatMessage[]>([
    {
      role: 'assistant',
      text:
        'Bonjour. Je suis l’assistant OCR spécialisé. Je peux analyser le dernier résultat, détecter les champs faibles, recommander un moteur OCR et exploiter l’historique. Active “Mode IA” pour utiliser Groq si la clé API est configurée.',
      suggestions: [
        'Analyse le dernier résultat OCR',
        'Donne-moi la dernière extraction faite',
        'Quels champs sont faibles ?',
        'Quel mode OCR utiliser ?',
        'Quel est le taux de succès global ?',
      ],
      severity: 'info',
      mode: 'rules',
    },
  ]);

  constructor(private assistant: AssistantApiService, public auth: AuthApiService) {
    this.hasLastResult.set(this.assistant.hasLastResult());
    this.checkStatus();
  }

  send(text?: string): void {
    const content = (text || this.message || '').trim();

    if (!content) return;

    const lastResult = this.includeLastResult ? this.assistant.getLastResult() : null;

    this.error.set(null);
    this.message = '';

    this.messages.update(items => [
      ...items,
      {
        role: 'user',
        text: content,
      },
    ]);

    this.loading.set(true);

    this.assistant.chat(content, lastResult, this.useAi, this.includeHistory).subscribe({
      next: response => {
        this.messages.update(items => [
          ...items,
          {
            role: 'assistant',
            text: response.reply,
            suggestions: response.suggestions,
            severity: response.severity,
            mode: response.mode,
            debug: response.debug,
          },
        ]);

        this.loading.set(false);
        this.hasLastResult.set(this.assistant.hasLastResult());
      },
      error: err => {
        this.error.set(err?.error?.detail || err?.message || 'Assistant indisponible.');
        this.loading.set(false);
      },
    });
  }

  checkStatus(): void {
    this.checkingStatus.set(true);

    this.assistant.status().subscribe({
      next: status => {
        this.assistantStatus.set(status);
        this.checkingStatus.set(false);
      },
      error: err => {
        this.assistantStatus.set({
          assistant: 'error',
          error: err?.error?.detail || err?.message || err,
        });
        this.checkingStatus.set(false);
      },
    });
  }

  refreshLastResult(): void {
    this.hasLastResult.set(this.assistant.hasLastResult());
  }

  modeLabel(): string {
    const status = this.assistantStatus();
    const llm = status?.llm;

    if (!llm) {
      return 'Statut IA inconnu';
    }

    if (!llm.llm_enabled) {
      return 'IA désactivée backend';
    }

    if (!llm.key_present) {
      return `Clé ${llm.key_name} absente`;
    }

    return `${llm.provider} · ${llm.model}`;
  }

  debugJson(value: any): string {
    try {
      return JSON.stringify(value || {}, null, 2);
    } catch {
      return String(value);
    }
  }
}