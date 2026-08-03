from pynput import keyboard as kb

def wait_for_hotkey():
    """Wait for a global keypress: Enter=approve, Esc=reject.
    Works without terminal focus - you can be in any window."""
    decision = {"answer": None}

    def on_press(key):
        if key == kb.Key.enter:
            decision["answer"] = "approve"
            return False        # stop listening
        elif key == kb.Key.esc:
            decision["answer"] = "reject"
            
            return False

    print(">>> Press ENTER (approve) or ESC (reject) - from ANY window")
    with kb.Listener(on_press=on_press) as listener:
        listener.join()
    return decision["answer"]

# test it
print("Testing global hotkey. Click into another window, then press Enter or Esc.")
result = wait_for_hotkey()
print(f"You chose: {result}")