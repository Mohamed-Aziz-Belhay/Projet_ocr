
/**
 * src/app/components/monitoring/monitoring.component.ts
 *
 * Tableau de bord operationnel natif Angular, alimente par Prometheus
 * via le backend (cf. routes_monitoring.py). Complementaire au tableau
 * de bord metier existant (historique des extractions) : celui-ci se
 * concentre sur la sante du pipeline OCR/IA en temps reel.
 */
import { Component, ElementRef, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Chart, ChartConfiguration, registerables } from 'chart.js';

import { MetricsSummary, MonitoringApiService } from './../../../services/monitoring-api.service';

Chart.register(...registerables);

@Component({
  selector: 'app-monitoring',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="monitoring-page">
      <h2>Supervision du pipeline</h2>

      <div *ngIf="!prometheusAvailable && loaded" class="warning-banner">
        Le service de supervision (Prometheus) est actuellement injoignable.
        Les indicateurs ci-dessous peuvent être incomplets ou obsolètes.
      </div>

      <div class="kpi-grid" *ngIf="summary as s">
        <div class="kpi-card">
          <span class="kpi-label">Confiance moyenne</span>
          <span class="kpi-value">{{ s.confidence_avg_pct }}%</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Débit d'extraction</span>
          <span class="kpi-value">{{ s.extraction_rate_per_sec }} req/s</span>
        </div>
        <div class="kpi-card" [class.kpi-alert]="s.circuit_breakers_open > 0">
          <span class="kpi-label">Moteurs en échec</span>
          <span class="kpi-value">{{ s.circuit_breakers_open }}</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Jobs actifs</span>
          <span class="kpi-value">{{ s.active_jobs }}</span>
        </div>
      </div>

      <div class="charts-row">
        <div class="chart-container">
          <h3>Durée de traitement (p95) par template</h3>
          <canvas #durationCanvas></canvas>
        </div>
        <div class="chart-container">
          <h3>Champs extraits — taux par issue</h3>
          <canvas #fieldsCanvas></canvas>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .monitoring-page { padding: 1.5rem; }
    .warning-banner {
      background: #fff3cd; color: #664d03; border: 1px solid #ffe69c;
      padding: 0.75rem 1rem; border-radius: 6px; margin-bottom: 1rem;
    }
    .kpi-grid {
      display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem;
      margin-bottom: 2rem;
    }
    .kpi-card {
      background: #f8f9fa; border-radius: 8px; padding: 1rem;
      display: flex; flex-direction: column; gap: 0.25rem;
    }
    .kpi-card.kpi-alert { background: #f8d7da; }
    .kpi-label { font-size: 0.85rem; color: #555; }
    .kpi-value { font-size: 1.6rem; font-weight: 600; }
    .charts-row {
      display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem;
    }
    .chart-container { background: #fff; border-radius: 8px; padding: 1rem; }
  `],
})
export class MonitoringComponent implements OnInit, OnDestroy {
  @ViewChild('durationCanvas') durationCanvas!: ElementRef<HTMLCanvasElement>;
  @ViewChild('fieldsCanvas') fieldsCanvas!: ElementRef<HTMLCanvasElement>;

  summary: MetricsSummary | null = null;
  prometheusAvailable = true;
  loaded = false;

  private durationChart?: Chart;
  private fieldsChart?: Chart;
  private refreshHandle?: ReturnType<typeof setInterval>;

  constructor(private monitoringApi: MonitoringApiService) {}

  ngOnInit(): void {
    this.fetchAndRender();
    // Rafraichissement automatique toutes les 30s, aligne sur le
    // "refresh: 30s" deja utilise par le dashboard Grafana de reference.
    this.refreshHandle = setInterval(() => this.fetchAndRender(), 30_000);
  }

  ngOnDestroy(): void {
    if (this.refreshHandle) {
      clearInterval(this.refreshHandle);
    }
    this.durationChart?.destroy();
    this.fieldsChart?.destroy();
  }

  private fetchAndRender(): void {
    this.monitoringApi.getMetricsSummary().subscribe({
      next: (data) => {
        this.summary = data;
        this.prometheusAvailable = data.prometheus_available;
        this.loaded = true;
        this.renderCharts(data);
      },
      error: () => {
        this.prometheusAvailable = false;
        this.loaded = true;
      },
    });
  }

  private renderCharts(data: MetricsSummary): void {
    this.renderDurationChart(data.duration_p95_by_template);
    this.renderFieldsChart(data.field_outcomes);
  }

  private renderDurationChart(series: MetricsSummary['duration_p95_by_template']): void {
    if (!this.durationCanvas) {
      return;
    }
    const config: ChartConfiguration = {
      type: 'bar',
      data: {
        labels: series.map((s) => s.label),
        datasets: [{
          label: 'p95 (secondes)',
          data: series.map((s) => s.value),
        }],
      },
      options: { responsive: true, plugins: { legend: { display: false } } },
    };

    if (this.durationChart) {
      this.durationChart.data = config.data;
      this.durationChart.update();
    } else {
      this.durationChart = new Chart(this.durationCanvas.nativeElement, config);
    }
  }

  private renderFieldsChart(series: MetricsSummary['field_outcomes']): void {
    if (!this.fieldsCanvas) {
      return;
    }
    const config: ChartConfiguration = {
      type: 'bar',
      data: {
        labels: series.map((s) => s.label),
        datasets: [{
          label: 'Taux par champ',
          data: series.map((s) => s.value),
        }],
      },
      options: {
        responsive: true,
        indexAxis: 'y',
        plugins: { legend: { display: false } },
      },
    };

    if (this.fieldsChart) {
      this.fieldsChart.data = config.data;
      this.fieldsChart.update();
    } else {
      this.fieldsChart = new Chart(this.fieldsCanvas.nativeElement, config);
    }
  }
}