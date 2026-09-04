"""Real GTK4 surface exposing actual rendered time, input and clipboard state."""

import gi
import json
from pathlib import Path
import sys
import time

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # noqa: E402 — bootstrap paths/GI version before importing

out = Path(sys.argv[1])
name = sys.argv[2]
app = Gtk.Application(application_id="space.passpage.RealmsProbe" + name)


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="Realm probe " + name)
    window.set_default_size(600, 360)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    box.set_margin_top(30)
    box.set_margin_start(30)
    box.set_margin_end(30)
    box.set_margin_bottom(30)
    clock = Gtk.Label(label=name)
    entry = Gtk.Entry(placeholder_text="Isolation marker")
    button = Gtk.Button(label="Record click")
    state = {"realm": name, "clicks": 0, "text": "", "tick": 0}

    def save():
        temporary = out.with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        temporary.replace(out)

    def clicked(button):
        state["clicks"] += 1
        save()
        button.set_label("Recorded " + str(state["clicks"]))

    def changed(entry):
        state["text"] = entry.get_text()
        save()

    def tick():
        state["tick"] += 1
        state["time"] = time.time()
        clock.set_label(f"{name} • frame {state['tick']} • {state['time']:.1f}")
        save()
        return True

    button.connect("clicked", clicked)
    entry.connect("changed", changed)
    box.append(clock)
    box.append(entry)
    box.append(button)
    window.set_child(box)
    window.present()
    GLib.timeout_add(500, tick)


app.connect("activate", activate)
app.run([])
