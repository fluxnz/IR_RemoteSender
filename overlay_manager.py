"""Overlay region management and rendering."""
import tkinter as tk
from tkinter import messagebox
from pathlib import Path


class OverlayManager:
    """Manages overlay regions for RTC-850 device."""
    
    def __init__(self, config):
        """
        Args:
            config: ConfigParser instance for loading/saving regions
        """
        self.config = config
        self.regions = {}
    
    def load_regions(self, device_name, actions):
        """Load regions from config for a device."""
        self.regions = {}
        sec = f"{device_name}_Regions"
        if sec in self.config:
            for key in list(self.config[sec]):
                try:
                    vals = [float(x) for x in self.config[sec][key].split(",")]
                    if len(vals) == 4:
                        exact_key = key if key in actions else next((act for act in actions if act.lower() == key.lower()), None)
                        if exact_key:
                            self.regions[exact_key] = tuple(vals)
                            if exact_key != key:
                                self.config[sec][exact_key] = self.config[sec][key]
                                del self.config[sec][key]
                except Exception:
                    pass
        return self.regions
    
    def save_region(self, device_name, action_name, x0, y0, x1, y1, image_width, image_height):
        """Save a region to config."""
        nx0, ny0 = min(x0, x1) / image_width, min(y0, y1) / image_height
        nx1, ny1 = max(x0, x1) / image_width, max(y0, y1) / image_height
        
        sec = f"{device_name}_Regions"
        if sec not in self.config:
            self.config[sec] = {}
        
        # Remove old case-insensitive duplicates
        old_keys = [k for k in self.config[sec] if k.lower() == action_name.lower() and k != action_name]
        for old_key in old_keys:
            del self.config[sec][old_key]
        
        self.config[sec][action_name] = f"{nx0},{ny0},{nx1},{ny1}"
        self.regions[action_name] = (nx0, ny0, nx1, ny1)
    
    def clear_regions(self, device_name):
        """Clear all regions for a device."""
        sec = f"{device_name}_Regions"
        if sec in self.config:
            del self.config[sec]
        self.regions = {}
    
    def get_regions(self):
        """Get all loaded regions."""
        return self.regions
