"""Cœur métier de mc-admin (hexagonal).

Ce package n'importe AUCUN détail d'I/O : ni FastAPI, ni docker, ni mcrcon, ni
yaml, ni accès fichier. Il ne connaît que ses Ports (interfaces) et son modèle.
Toute violation de cette règle est un bug d'architecture (cf. CLAUDE.md §6).
"""
