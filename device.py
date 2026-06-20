"""Device abstraction for IR remote commands."""


class Device:
    """Represents an IR remote device with actions and HEX codes."""
    
    def __init__(self, name, actions):
        """
        Args:
            name: Device name (e.g., "RA12", "RTC 850")
            actions: Dict mapping action names to IR HEX codes
                     e.g., {"Power": "0000 006c ...", "Volume Up": "0000 006c ..."}
        """
        self.name = name
        self.actions = actions
    
    def get_command(self, action_name):
        """Get the IR HEX code for an action."""
        return self.actions.get(action_name)
    
    def has_action(self, action_name):
        """Check if device has an action."""
        return action_name in self.actions
    
    def get_actions(self):
        """Get all action names for this device."""
        return list(self.actions.keys())
