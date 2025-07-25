# Installation de PySide6

PySide6 est un ensemble de liaisons Python pour le framework Qt6, permettant de créer des interfaces graphiques modernes.

## Prérequis

## Fonctionnalités supplémentaires

- Résolution de nom d'hôte : permet d'utiliser des noms d'hôtes au lieu d'adresses IP longues pour plus de simplicité.
- Utilisation d'un fichier `p2p_hosts.txt` : ce fichier est utilisé par l'application et se met à jour automatiquement lors de la découverte de nouveaux pairs.
- Python 3.6 ou supérieur
- pip (gestionnaire de paquets Python)
- (Optionnel mais recommandé) virtualenv ou venv pour isoler l'environnement Python

## Installation

Ouvrez un terminal et exécutez les commandes suivantes :

# Créez un environnement virtuel (remplacez 'env' par le nom souhaité)
python -m venv env

# Activez l'environnement virtuel
# Sous Linux/macOS :
source env/bin/activate
# Sous Windows :
env\Scripts\activate

# Installez PySide6 dans l'environnement virtuel
pip install PySide6
