"""Shared retailer registry for Bargin.

Provides functions to load, save, and manage retailer search configurations.
"""

import yaml
import os
from pathlib import Path
from typing import Dict, Any, Optional

# Get the project root directory (parent of bargin/ directory)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_DIR = PROJECT_ROOT / "registry"


def get_registry_path() -> Path:
    """Get the path to the registry directory."""
    return REGISTRY_DIR


def load_retailers() -> Dict[str, Any]:
    """
    Load retailer search configurations.
    
    Returns a dict with 'curated' and 'local' keys containing retailer configurations.
    """
    curated_path = REGISTRY_DIR / "retailers.yaml"
    local_path = REGISTRY_DIR / "local.yaml"
    
    retailers = {}
    
    # Load curated retailers (built-in)
    if curated_path.exists():
        with open(curated_path) as f:
            retailers['curated'] = yaml.safe_load(f) or {}
    
    # Load local retailers (user-defined)
    if local_path.exists():
        with open(local_path) as f:
            retailers['local'] = yaml.safe_load(f) or {}
    
    return retailers


def save_retailer(name: str, config: Dict[str, Any]) -> None:
    """
    Save or update a retailer configuration.
    
    Retailers are saved to the local registry file.
    """
    REGISTRY_DIR.mkdir(exist_ok=True)
    
    local_path = REGISTRY_DIR / "local.yaml"
    retailers = load_retailers()
    
    # Ensure local dict exists
    if 'local' not in retailers:
        retailers['local'] = {}
    
    # Save the retailer configuration
    retailers['local'][name] = config
    
    with open(local_path, 'w') as f:
        yaml.dump(retailers, f)


def get_search_block(retailer: str) -> Optional[Dict[str, Any]]:
    """
    Get a retailer's search block configuration.
    
    Searches both curated and local registries, with local taking precedence.
    """
    retailers = load_retailers()
    
    # First check local (user-defined)
    if 'local' in retailers and retailer in retailers['local']:
        return retailers['local'][retailer]
    
    # Then check curated (built-in)
    if 'curated' in retailers and retailer in retailers['curated']:
        return retailers['curated'][retailer]
    
    return None


def list_retailers() -> Dict[str, list]:
    """
    List all available retailers.
    
    Returns a dict with 'curated' and 'local' lists of retailer names.
    """
    retailers = load_retailers()
    
    return {
        'curated': list(retailers.get('curated', {}).keys()),
        'local': list(retailers.get('local', {}).keys())
    }