import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuthApiService } from '../../services/auth-api.service';

interface StatItem {
  value: string;
  label: string;
}

interface RoleCard {
  key: string;
  name: string;
  badgeClass: string;
  items: string[];
}

interface FaqItem {
  question: string;
  answer: string;
  open: boolean;
}

@Component({
  selector: 'ocr-home',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './home.component.html',
  styleUrl: './home.component.css',
})
export class HomeComponent {
  constructor(public auth: AuthApiService) {}

  stats: StatItem[] = [
    { value: '16', label: 'Routers FastAPI' },
    { value: '4', label: 'Types de documents' },
    { value: '3', label: 'Moteurs OCR' },
    { value: '88%+', label: 'Accuracy Swin' },
  ];

  // 3 rôles uniquement — Viewer supprimé
  roles: RoleCard[] = [
    {
      key: 'admin',
      name: 'Admin',
      badgeClass: 'role-admin',
      items: ['Gestion des utilisateurs', 'Historique global', 'Dashboard analytique', 'Modèles Swin / YOLO'],
    },
    {
      key: 'operator',
      name: 'Operator',
      badgeClass: 'role-operator',
      items: ['Extraction', 'Templates YAML', 'Assistant OCR', 'Scanner Agent'],
    },
    {
      key: 'simple_user',
      name: 'Simple User',
      badgeClass: 'role-simple',
      items: ['Extraction', 'Son historique', 'Gestion du profil'],
    },
  ];

  faq: FaqItem[] = [
    {
      question: 'Quels types de documents puis-je analyser ?',
      answer: 'Passeports, CIN tunisiennes, factures tunisiennes et registres de commerce. La détection du type peut être automatique (mode auto) ou manuelle.',
      open: false,
    },
    {
      question: 'Quels formats de fichiers sont acceptés ?',
      answer: 'Images (JPG, PNG) et PDF. La taille maximale par fichier est de 25 Mo.',
      open: false,
    },
    {
      question: 'Comment récupérer mes résultats ?',
      answer: "Chaque extraction génère un JSON structuré téléchargeable, ainsi qu'un export CSV ou PDF. Vous pouvez aussi les retrouver dans votre historique.",
      open: false,
    },
    {
      question: 'Mes données sont-elles sécurisées ?',
      answer: "Oui. La plateforme fonctionne en mode on-premise (vos documents ne quittent jamais le serveur), avec authentification JWT, mots de passe hachés (PBKDF2-SHA256) et contrôle d'accès par rôle.",
      open: false,
    },
    {
      question: 'Dois-je créer un compte pour extraire un document ?',
      answer: 'Oui. Chaque extraction est associée à un utilisateur identifié pour garantir la traçabilité. La création de compte est gratuite et immédiate.',
      open: false,
    },
  ];

  toggleFaq(item: FaqItem): void {
    item.open = !item.open;
  }

  isLoggedIn(): boolean {
    return this.auth.isLoggedIn();
  }
}