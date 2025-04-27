import tkinter as tk
from tkinter import ttk

def show_description(tree, video_urls, search_window, status_var, status_label, video_descriptions):
    print("Showing description")

root = tk.Tk()
tree = ttk.Treeview(root)
video_urls = {}
search_window = tk.Toplevel(root)
status_var = tk.StringVar()
status_label = ttk.Label(root)
video_descriptions = {}

context_menu = tk.Menu(tree, tearoff=0)
context_menu.add_command(label="Показать описание", 
                        command=lambda: show_description(tree, video_urls, search_window, status_var, status_label, video_descriptions))

tree.bind("<Button-3>", lambda event: context_menu.post(event.x_root, event.y_root))
root.mainloop()