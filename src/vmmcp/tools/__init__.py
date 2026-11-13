"""tools/ — one module per tool family. Importing a module registers its @mcp.tool() functions.

  shell        shell_run, shell_run_elevated, shell_run_bg, shell_run_interactive
  files        file_*, archive_*
  processes    process_list / start / kill
  clipboard    clipboard_get / set
  registry     registry_read / write / delete / list
  services     service_status / control / list
  network      net_*, firewall_rule_*
  system       vm_info, event_log_read, scheduled_task_*, env_*, installed_drivers, toast_notify
  software     winget_*, download_file, install_msi
  server_admin server_version / update / restart / log_tail
  power        reboot, reboot_cancel, shutdown
  knowledge    vm_guide (serves skill/SKILL.md to the AI)
  computer     screenshot, mouse_*, keyboard_*, ui_tree, ... (AI computer use)
"""

from . import (clipboard, computer, files, knowledge, network, power, processes, registry,  # noqa: F401
               server_admin, services, shell, software, system)
