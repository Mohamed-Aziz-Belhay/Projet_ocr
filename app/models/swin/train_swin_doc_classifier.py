#app/models/swin/train_swin_doc_classifier.py
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Tuple

import timm
import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms


class ManifestDataset(Dataset):
    def __init__(self, manifest_path: Path, label_to_id: Dict[str, int], image_size: int, train: bool):
        self.items = []
        with manifest_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    if Path(item["image_path"]).exists():
                        self.items.append(item)
        self.label_to_id = label_to_id
        aug = [transforms.Resize((image_size, image_size))]
        if train:
            aug += [
                transforms.RandomApply([transforms.ColorJitter(brightness=0.15, contrast=0.15, saturation=0.10)], p=0.35),
                transforms.RandomRotation(3),
            ]
        aug += [
            transforms.ToTensor(),
            transforms.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
        ]
        self.tf = transforms.Compose(aug)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx: int):
        item = self.items[idx]
        with Image.open(item["image_path"]) as img:
            x = self.tf(img.convert("RGB"))
        y = self.label_to_id[item["class_name"]]
        return x, torch.tensor(y, dtype=torch.long)


def evaluate(model, loader, device) -> Tuple[float, float]:
    model.eval()
    loss_fn = nn.CrossEntropyLoss()
    total_loss = 0.0
    total = 0
    correct = 0
    with torch.no_grad():
        for x, y in loader:
            x = x.to(device)
            y = y.to(device)
            logits = model(x)
            loss = loss_fn(logits, y)
            total_loss += float(loss.item()) * x.size(0)
            total += x.size(0)
            correct += int((logits.argmax(dim=1) == y).sum().item())
    return total_loss / max(total, 1), correct / max(total, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-dir", default="app/data/external/midv2020/prepared/upright")
    parser.add_argument("--out-dir", default="models/swin_doc_classifier")
    parser.add_argument("--model-name", default="swin_tiny_patch4_window7_224")
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=3e-5)
    parser.add_argument("--num-workers", type=int, default=0)
    args = parser.parse_args()

    prepared_dir = Path(args.prepared_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (prepared_dir / "labels.json").open("r", encoding="utf-8") as f:
        labels_data = json.load(f)
    labels = labels_data["labels"]
    label_to_id = {k: int(v) for k, v in labels_data["label_to_id"].items()}
    train_ds = ManifestDataset(prepared_dir / "train.jsonl", label_to_id, args.image_size, True)
    val_ds = ManifestDataset(prepared_dir / "val.jsonl", label_to_id, args.image_size, False)
    if len(train_ds) == 0 or len(val_ds) == 0:
        raise RuntimeError("No train/val images found. Run prepare_midv2020.py and check image_path values.")
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=args.num_workers)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, num_workers=args.num_workers)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[INFO] device={device}")
    model = timm.create_model(args.model_name, pretrained=True, num_classes=len(labels)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    loss_fn = nn.CrossEntropyLoss()
    best_acc = 0.0
    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        total = 0
        correct = 0
        for x, y in train_loader:
            x = x.to(device)
            y = y.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(x)
            loss = loss_fn(logits, y)
            loss.backward()
            optimizer.step()
            total_loss += float(loss.item()) * x.size(0)
            total += x.size(0)
            correct += int((logits.argmax(dim=1) == y).sum().item())
        train_loss = total_loss / max(total, 1)
        train_acc = correct / max(total, 1)
        val_loss, val_acc = evaluate(model, val_loader, device)
        print(f"epoch={epoch} train_loss={train_loss:.4f} train_acc={train_acc:.4f} val_loss={val_loss:.4f} val_acc={val_acc:.4f}")
        if val_acc >= best_acc:
            best_acc = val_acc
            torch.save({"model_name": args.model_name, "state_dict": model.state_dict(), "labels": labels, "label_to_id": label_to_id, "image_size": args.image_size}, out_dir / "best.pt")
            with (out_dir / "labels.json").open("w", encoding="utf-8") as f:
                json.dump(labels_data, f, ensure_ascii=False, indent=2)
            print(f"[OK] saved best model acc={best_acc:.4f}")
    print(f"[DONE] best_acc={best_acc:.4f}")


if __name__ == "__main__":
    main()
