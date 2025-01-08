import subprocess
import argparse
import os
import sys
import yaml
import xml.etree.ElementTree as ET

def subprocess_run_wrapper(command, dry_run=None, **kwargs):
    """
    Wrapper for subprocess.run to support dry-run mode.

    Parameters:
    - command (list): The command to be executed as a list of strings.
    - dry_run (bool): If True, the command is only printed and not executed.
    - kwargs: Additional keyword arguments passed to subprocess.run.

    Returns:
    - subprocess.CompletedProcess: The result of subprocess.run (or None in dry-run mode).
    """
    if dry_run is None:  # Default to the global value if not explicitly set
        dry_run = defaultconfig.get('dry_run', False)    

    command_str = " ".join(command)  # Convert the command list to a readable string
    if dry_run:
        print(f"[DRY-RUN] Command: {command_str}")
        return None  # Return None in dry-run mode
    else:
        print(f"[EXECUTING] Command: {command_str}")
        return subprocess.run(command, **kwargs)

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="VM creation script using virt-install.")
    parser.add_argument(
        "-c", "--vmconffile",
        type=str,   
        required=True,  
        help="Path to the YAML configuration file."
    )
    parser.add_argument(
        "-r", "--remove",
        action="store_true",  # Makes it a boolean flag
        help="Remove the specified VM configuration (optional)."
    )
    return parser.parse_args()

def load_config(conffile: str) -> dict:
    """Load and return YAML configuration."""
    if not os.path.exists(conffile):
        print(f"[ERROR] YAML configuration file {conffile} not found.")
        sys.exit(1)
    with open(conffile, "r") as file:
        return yaml.safe_load(file)

def is_mountpoint(path: str) -> bool:
    """
    Check if a given path is a mount point.

    Parameters:
    - path (str): The path to check.

    Returns:
    - bool: True if the path is a mount point, False otherwise.
    """
    if os.path.ismount(path):
        print(f"[INFO] {path} is a mount point.")
        return True
    else:
        print(f"[INFO] {path} is not a mount point.")
        return False


def is_ramdisk(path: str) -> bool:
    """
    Check if the mount point is a ramdisk (tmpfs).

    Parameters:
    - path (str): The mount point to check.

    Returns:
    - bool: True if the mount point is a ramdisk (tmpfs), False otherwise.
    """
    try:
        with open("/proc/mounts", "r") as mounts_file:
            for line in mounts_file:
                parts = line.split()
                mount_point = parts[1]  # The second field is the mount point
                if os.path.normpath(mount_point) == os.path.normpath(path):
                    if parts[2] == "tmpfs":
                        print(f"[INFO] {path} is a ramdisk (tmpfs).")
                        return True
                    else:
                        print(f"[INFO] {path} is not a ramdisk (tmpfs).")
                        return False
    except FileNotFoundError:
        print("[ERROR] /proc/mounts not found. Are you running on Linux?")
    except Exception as e:
        print(f"[ERROR] An error occurred: {e}")

    print(f"[INFO] {path} is not a ramdisk (tmpfs).")
    return False


def has_correct_size(path: str, expected_size_gb: int) -> bool:
    """
    Check if a ramdisk (tmpfs) has the correct size.

    Parameters:
    - path (str): The ramdisk mount point to check.
    - expected_size_gb (int): The expected size in GB.

    Returns:
    - bool: True if the ramdisk has the correct size, False otherwise.
    """
    try:
        with open("/proc/mounts", "r") as mounts_file:
            for line in mounts_file:
                parts = line.split()
                mount_point = parts[1]  # The second field is the mount point
                if os.path.normpath(mount_point) == os.path.normpath(path):
                    options = parts[3].split(",")
                    size_option = next((opt for opt in options if opt.startswith("size=")), None)
                    if size_option:
                        size_kb = int(size_option.split("=")[1].strip("k"))
                        size_gb = size_kb / (1024 * 1024)
                        if size_gb == expected_size_gb:
                            print(f"[INFO] {path} has the correct size of {expected_size_gb} GB.")
                            return True
                        else:
                            print(f"[INFO] {path} has size {size_gb:.2f} GB (expected {expected_size_gb} GB).")
                            return False
                    else:
                        print(f"[WARN] {path} does not have a size specified.")
                        return False
    except FileNotFoundError:
        print("[ERROR] /proc/mounts not found. Are you running on Linux?")
    except Exception as e:
        print(f"[ERROR] An error occurred: {e}")

    print(f"[INFO] {path} size information not found.")
    return False

def mount_ramdisk(ramdisk_path: str, ramdisk_size: int) -> None:
    """Ensure the RAM disk is mounted."""

    mount_ramdisk = False
    if not is_mountpoint(ramdisk_path):
        mount_ramdisk = True
    if not mount_ramdisk and not is_ramdisk(ramdisk_path):
        print(f"[ERROR] Ramdisk path {ramdisk_path} is a mountpoint but not a ramdisk - Exiting")
        sys.exit(1)
    if not mount_ramdisk and not has_correct_size(ramdisk_path, ramdisk_size):
        print(f"[ERROR] Ramdisk path {ramdisk_path} is a mountpoint but does not have correct size - Exiting")
        sys.exit(1)

    if mount_ramdisk:
        print(f"[INFO] Mounting tmpfs on {ramdisk_path} with size={ramdisk_size}G")
        # need sudo here
        #os.makedirs(ramdisk_path, exist_ok=True)
        subprocess_run_wrapper(["sudo", "mkdir", "-p", ramdisk_path], check=True)
        subprocess_run_wrapper([
            "sudo", "mount", 
            "-t", "tmpfs", 
            "-o", f"size={ramdisk_size}G",
            "tmpfs", 
            ramdisk_path
        ], check=True)

def remove_vm(vm_name: str) -> None:
    """Destroy and undefine an existing VM."""
    print(f"[INFO] Destroying VM '{vm_name}' if it exists.")
    subprocess_run_wrapper([
        "sudo", "virsh", 
        "--connect=qemu:///system",
        "destroy", 
        vm_name], 
        stderr=subprocess.DEVNULL)

    print(f"[INFO] Undefining VM '{vm_name}' if it exists.")
    subprocess_run_wrapper([
        "sudo", "virsh", 
        "--connect=qemu:///system",
        "undefine", vm_name,
        "--managed-save",
        "--remove-all-storage",
        "--delete-storage-volume-snapshots",
        "--snapshots-metadata",
        "--checkpoints-metadata",
        "--nvram"
    ], stderr=subprocess.DEVNULL)

def create_disk(disk_uri: str, size: int, disk_format: str) -> None:
    print(f"[INFO] Creating disk at {disk_uri} with size {size}GiB and format {disk_format}")
    subprocess_run_wrapper(
            [
                "sudo", "qemu-img", "create", 
                "-f", 
                disk_format, 
                "-o", 
                "preallocation=off", 
                disk_uri, 
                f"{size}G"
            ], 
            check=True)


def convert_disk(disk_uri_in: str, disk_uri_out: str, disk_format_in: str, disk_format_out: str) -> None:
    """
    Convert an disk to a disk with sparse allocation.

    Parameters:
    - disk_uri_in (str): Path to the input disk.
    - disk_uri_out (str): Path to the output disk.
    - disk_format_in (str): Format of the input disk (e.g., "qcow2", "raw").
    - disk_format_out (str): Format of the output disk (e.g., "qcow2", "raw").
    """
    try:
        subprocess_run_wrapper(
            [
                "sudo", "qemu-img", "convert",
                "-p",
                "-f", disk_format_in,  # Format of the input image (e.g. raw, qcow2)
                "-O", disk_format_out,  # Output format (e.g., raw, qcow2)
                "-o", "preallocation=off",  # Sparse allocation for the output disk
                disk_uri_in,
                disk_uri_out
            ],
            check=True
        )
        print(f"[INFO] Converted image '{disk_uri_in}' to disk '{disk_uri_out}' with format '{disk_format_out}' and sparse allocation.")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to convert image '{disk_uri_in}' to disk '{disk_uri_out}': {e}")
        sys.exit(1)


def resize_disk(disk_uri: str, new_size: int, disk_format: str) -> None:
    """
    Resize a disk image with sparse allocation and no preallocation.

    Parameters:
    - disk_uri (str): Path to the disk image.
    - new_size (str): New size of the disk image in GiB(e.g., "40").
    - disk_format (str): Format of the disk image (e.g., "qcow2", "raw").
    """
    try:
        subprocess_run_wrapper(
            [
                "sudo", "qemu-img", "resize",
                "-f", disk_format,
                "-o", "preallocation=off",  # Ensures sparse allocation
                disk_uri, 
                f"{new_size}G"
            ],
            check=True
        )
        print(f"[INFO] Resized disk '{disk_uri}' to {new_size} with sparse allocation.")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to resize disk '{disk_uri}': {e}")

def remove_disk(disk) -> None:
    # Remove disk 
    if os.path.exists(disk['uri']):
        print(f"[INFO] Removing old disk at {disk['uri']}")
        # we need sudo here
        #os.remove(disk['uri'])
        subprocess_run_wrapper(["sudo", "rm", disk['uri']],check=True)
 

def recreate_disk(disk) -> None:
    # Handle disk 
    if 'imgfile' in disk and os.path.isfile(disk['imgfile']):
        print(f"[INFO] Converting disk image from {disk['imgfile']} to {disk['uri']}")
        if not os.path.exists(disk['imgfile']):
            print(f"[ERROR] Disk image {disk['imgfile']} does not exist. Exiting.")
            sys.exit(1)
        convert_disk(
            disk_uri_in=disk['imgfile'],
            disk_uri_out=disk['uri'],
            disk_format_in=disk['imgformat'],  # Adjust if input format varies
            disk_format_out=disk['format']
        )
        if 'size' in disk:
            resize_disk(
                disk_uri=disk['uri'],
                new_size=disk['size'],
                disk_format=disk['format']
                )
    else:
        create_disk(disk['uri'], disk['size'], disk['format'])

def create_ignition_config() -> None:
    # TODO
    pass


def handle_vm_type(vm_type) -> None:
    match vm_type:
        case "linux":
            print("[INFO] Handling Linux VM")
            # Linux-specific setup logic
        case "windows":
            print("[INFO] Handling Windows VM")
            # Windows-specific setup logic
        case "flatcar":
            print("[INFO] Handling Flatcar VM")
            # Flatcar-specific setup logic
            create_ignition_config()
        case "generic":
            print("[INFO] Handling Generic VM")
            # Generic VM setup logic
        case _:
            print(f"[ERROR] Unknown VM type: {vm_type} - Exiting")
            sys.exit(1)


def set_disk_removable(xml: str, disk) -> str:
    print(f"[DEBUG]: disk['uri'] {disk['uri']}")
    root = ET.fromstring(xml)
    
    # Find the disk_element element with the specific source file
    for disk_element in root.findall(".//disk"):
        source = disk_element.find("source")
        print(f"[DEBUG]: source.attrib.get('file') {source.attrib.get('file')}")
        if source is not None and source.attrib.get("file") == disk['uri']:
            target = disk_element.find("target")
            if target is not None and target.attrib.get("dev") and target.attrib.get("bus"):
                # Add the "removable" attribute
                print(f"disk_element b4:\n{ET.tostring(disk_element, encoding='unicode')}")
                target.set("removable", "on")
                print(f"disk_element after:\n{ET.tostring(disk_element, encoding='unicode')}")
    
    # Convert the modified XML tree back to a string
    return ET.tostring(root, encoding="unicode")

    

def main():
    global defaultconfig
    defaultconffile = "virtbuilder.conf"
    defaultconfig = load_config(defaultconffile)
    print(f"defaultconfig:\n{yaml.dump(defaultconfig)}")

    # Parse arguments
    args = parse_args()
    vmconffile = args.vmconffile
    remove = args.remove

    vmconffile_base, vmconffile_ext  = os.path.splitext(vmconffile)
    print(f"[DEBUG] vmconffile {vmconffile} extension is: '{vmconffile_ext}'") 
    if vmconffile_ext not in [".yml", ".yaml"]:
        print(f"[ERROR] vmconffile {vmconffile} must have extension .yml or .yaml - Exiting")
        sys.exit(1)
    vmvirtfile = f"{vmconffile_base}.xml"

    # Load vmconfig
    vmconfig = load_config(vmconffile)
    print(f"vmconfig:\n{yaml.dump(vmconfig)}")

    vm = vmconfig['vm']
    disks = vmconfig['disks']
    ramdisk = vmconfig['ramdisk']
    network = vmconfig['network']

    # TODO
    print("\nVM type")
    #handle_vm_type(vm['type'])

    # Ensure RAM disk is mounted
    print("\nRAM disk")
    mount_ramdisk(ramdisk['path'], ramdisk['size'])

    # Destroy and undefine existing VM
    print("\nDestroy old VM and disks")
    remove_vm(vm['name'])
    for disk_key, disk_value in disks.items():
        remove_disk(disk_value)
    if remove:
        print(f"[INFO] remove cli argument given - Exiting after remove")
        sys.exit(0)


    print("\n(Re)create disks")
    for disk_key, disk_value in disks.items():
        recreate_disk(disk_value)

    # Build virt-install command
    print("\nCreate virt-install command")
    virtinstall_cmd = [
        "sudo", "virt-install",
        "--connect=qemu:///system",
        "--noautoconsole",
        "--print-xml",
        "--hvm",
        "--cpu=host-model",
        "--features=kvm_hidden=on",
        f"--os-variant={vm['os_variant']}",
        f"--name={vm['name']}",
        f"--vcpus={vm['vcpus']}",
        f"--memory={vm['memory']}",
    ]

    if vm.get('usbver', False) == "2":
        virtinstall_cmd.append("--controller=usb2")

    # BIOS
    print("\nBIOS")
    match vm['bios']:
        case "efi":
            if vm['secureboot']:
                virtinstall_cmd.append("--boot=uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes,firmware.feature1.name=enrolled-keys,firmware.feature1.enabled=yes")
            else:
                virtinstall_cmd.append("--boot=uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=no")
        case "legacy":
            virtinstall_cmd.append("-boot=uefi=off")
        case _:
            print(f"[ERROR] unknown bios {vm['bios']} - Exiting")
            sys.exit(1)

    # Graphics
    print("\nGraphics")
    match vm['graphics']:
        case "3d":
            virtinstall_cmd.append(f"--graphics=spice,listen=none,gl.enable=yes")
            virtinstall_cmd.append(f"--video=virtio,model.acceleration.accel3d=yes")
        case "spice":
            virtinstall_cmd.append(f"--graphics=spice")
        case "vnc":
            virtinstall_cmd.append(f"--graphics=vnc")
        case "serial_console":
            virtinstall_cmd.append(f"--graphics=none")
        case _:
            print(f"[ERROR] Unknown graphics {vm['graphics']} - Exiting.")
            sys.exit(1)


    # Add disks to virt-install
    print("\nDisks")
    scsi_controller = False
    for disk_key, disk_value in disks.items():
        bus = disk_value.get('bus','scsi')
        disk_snippet = f"--disk=path={disk_value['uri']},format={disk_value['format']},bus={bus},cache=writethrough,driver.discard='unmap',io=threads,sparse=yes"
        #disk_snippet+=f",size={disk_value['size']}"
        if disk_value.get('readonly', False):
            disk_snippet+=",readonly=yes"
        if bus == 'scsi':
            scsi_controller = True
        virtinstall_cmd.append(disk_snippet)
    virtinstall_cmd.append(f"--check=disk_size=off")
    if scsi_controller:
        virtinstall_cmd.append(f"--controller=type=scsi,model=virtio-scsi")
        

    # Add network configuration
    print("\nNetwork")
    match network['type']:
        case "nat":
            virtinstall_cmd.append(f"--network=network=default,mac={network['mac']},model={network.get('model', 'virtio')}")
        case "isolated":
            virtinstall_cmd.append(f"--network=network=isolated,mac={network['mac']},model={network.get('model', 'virtio')}")
        case "bridge":
            virtinstall_cmd.append(f"--network=bridge={network['parent_interface']},mac={network['mac']},model={network.get('model', 'virtio')}")
        case "macvtap":
            virtinstall_cmd.append(f"--network=type=direct,source={network['parent_interface']},source_mode=bridge,mac={network['mac']},model={network.get('model', 'virtio')}")
        case "ipvtap":
            # Check if ipvtap0 exists
            ipvtap_exists = subprocess_run_wrapper(
                ["ip", "link", "show", "ipvtap0"],
                capture_output=True,
                text=True,
                dry_run=False  # Replace with your dry-run config if needed
            )
            
            if ipvtap_exists is None or ipvtap_exists.returncode != 0:
                print("[INFO] Creating ipvtap0 interface...")
                subprocess_run_wrapper(
                    [
                        "ip", "link", "add",
                        "name", "ipvtap0",
                        "link", network['parent_interface'],
                        "type", "ipvtap",
                        "mode", "l2", "bridge"
                    ],
                    check=True
                )
            else:
                print("[INFO] ipvtap0 interface already exists.")
    
            # Check if ipvtap0 is up
            ipvtap_status = subprocess_run_wrapper(
                ["ip", "link", "show", "ipvtap0"],
                capture_output=True,
                text=True,
                dry_run=False  # Replace with your dry-run config if needed
            )
            
            if ipvtap_status and "UP" not in ipvtap_status.stdout:
                print("[INFO] Bringing ipvtap0 interface up...")
                subprocess_run_wrapper(
                    ["ip", "link", "set", "up", "ipvtap0"],
                    check=True
                )
            else:
                print("[INFO] ipvtap0 interface is already up.")
    
            virtinstall_cmd.append(f"--network=type=direct,source={network['parent_interface']},source_mode=bridge,mac={network['mac']},model={network.get('model', 'virtio')}")
        case _:
            print(f"[ERROR] Unknown network type: {network['type']} - Exiting")
            sys.exit(1)

    print("\n[INFO] Running virt-install command:")
    print("\n".join(virtinstall_cmd))
    print("\n")
    vm_ret = subprocess_run_wrapper(virtinstall_cmd, capture_output=True, text=True)

    # Check for errors in stderr or non-zero return code
    if vm_ret.returncode != 0 or vm_ret.stderr:
        print(f"[ERROR] Command failed with return code {vm_ret.returncode}.")
        print(f"[INFO] stdout: {vm_ret.stdout.strip()}")
        print(f"[ERROR] stderr: {vm_ret.stderr.strip()}")
        sys.exit(1)

    # Capture the XML output from stdout
    vm_xml = vm_ret.stdout

    # adapt options not supported by virt-install directly in xml
    # disk removable
    for disk_key, disk_value in disks.items():
        if disk_value.get('removable', False):
            vm_xml = set_disk_removable(xml = vm_xml, disk = disk_value)

    # Write the XML to a file
    try:
        with open(vmvirtfile, "w") as file:
            file.write(vm_xml)
        print(f"[INFO] VM XML written to {vmvirtfile}")
    except IOError as e:
        print(f"[ERROR] Failed to write VM XML to {vmvirtfile}: {e}")
        sys.exit(1)

    
    subprocess_run_wrapper(["sudo", "virsh", "--connect=qemu:///system", "define", vmvirtfile], check=True)
    subprocess_run_wrapper(["sudo", "virsh", "--connect=qemu:///system", "start", vm['name']], check=True)
    subprocess_run_wrapper(["virt-viewer", "--connect=qemu:///system", "--attach", vm['name']], check=True)


if __name__ == "__main__":
    main()
