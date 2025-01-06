import subprocess
import argparse
import os
import sys
import yaml

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
        "--vmconffile",
        type=str,
        required=True,
        help="Path to the YAML configuration file."
    )
    return parser.parse_args()

def load_config(conffile: str) -> dict:
    """Load and return YAML configuration."""
    if not os.path.exists(conffile):
        print(f"[ERROR] YAML configuration file {conffile} not found.")
        sys.exit(1)
    with open(conffile, "r") as file:
        return yaml.safe_load(file)

def mount_ramdisk(ramdisk_path: str, ramdisk_size: str) -> None:
    """Ensure the RAM disk is mounted."""
    os.makedirs(ramdisk_path, exist_ok=True)

    mount_output = subprocess_run_wrapper(["mount"], capture_output=True, text=True )
    
    if mount_output is not None:
        mount_output = mount_output.stdout
        print("[INFO] Mount output captured successfully.")
    else:
        mount_output = f"on{ramdisk_path} type tmpfs"
        print(f"[INFO] Dry-run mode; mount output is simulated - setting it to 'on {ramdisk_path} type tmpfs'")    

    print(f"[DEBUG] mount_output: {repr(mount_output)}")
    print(f"[DEBUG] Expected: {repr(f'on {ramdisk_path} type tmpfs')}")

    if f"on {ramdisk_path} type tmpfs" not in mount_output:
        print(f"[INFO] Mounting tmpfs on {ramdisk_path} with size={ramdisk_size}")
        subprocess_run_wrapper([
            "mount", "-t", "tmpfs", "-o", f"size={ramdisk_size}",
            "tmpfs", ramdisk_path
        ], check=True)
    else:
        print(f"[INFO] {ramdisk_path} is already mounted.")

def remove_vm(vm_name: str) -> None:
    """Destroy and undefine an existing VM."""
    print(f"[INFO] Destroying VM '{vm_name}' if it exists.")
    subprocess_run_wrapper(["virsh", "destroy", vm_name], stderr=subprocess.DEVNULL)

    print(f"[INFO] Undefining VM '{vm_name}' if it exists.")
    subprocess_run_wrapper([
        "virsh", "undefine", vm_name,
        "--managed-save",
        "--remove-all-storage",
        "--delete-storage-volume-snapshots",
        "--snapshots-metadata",
        "--checkpoints-metadata",
        "--nvram"
    ], stderr=subprocess.DEVNULL)

def create_disk(disk_uri: str, size: str, disk_format: str) -> None:
    print(f"[INFO] Creating disk at {disk_uri} with size {size} and format {disk_format}")
    subprocess_run_wrapper(
            [
                "qemu-img", "create", 
                "-f", 
                disk_format, 
                "-o", 
                "preallocation=off", 
                disk_uri, 
                size
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
                "qemu-img", "convert",
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


def resize_disk(disk_uri: str, new_size: str, disk_format: str) -> None:
    """
    Resize a disk image with sparse allocation and no preallocation.

    Parameters:
    - disk_uri (str): Path to the disk image.
    - new_size (str): New size of the disk image (e.g., "40G").
    - disk_format (str): Format of the disk image (e.g., "qcow2", "raw").
    """
    try:
        subprocess_run_wrapper(
            [
                "qemu-img", "resize",
                "-f", disk_format,
                "-o", "preallocation=off",  # Ensures sparse allocation
                disk_uri, 
                new_size
            ],
            check=True
        )
        print(f"[INFO] Resized disk '{disk_uri}' to {new_size} with sparse allocation.")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to resize disk '{disk_uri}': {e}")

def recreate_disk(disk) -> None:
    # Handle disk 
    if os.path.exists(disk['uri']):
        print(f"[INFO] Removing old disk at {disk['uri']}")
        os.remove(disk['uri'])
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


def handle_vm_type(vm_type):
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
        case "generic":
            print("[INFO] Handling Generic VM")
            # Generic VM setup logic
        case _:
            print(f"[WARN] Unknown VM type: {vm_type}")
            # Handle unknown cases



def main():
    global defaultconfig
    defaultconffile = "virt-builder.conf"
    defaultconfig = load_config(defaultconffile)
    print(f"defaultconfig: {defaultconfig}")

    # Parse arguments
    args = parse_args()
    vmconffile = args.vmconffile

    # Load vmconfig
    vmconfig = load_config(vmconffile)


    print(f"vmconfig: {vmconfig}")

    exit 
    vm = vmconfig['vm']
    disks = vmconfig['disks']
    ramdisk = vmconfig['ramdisk']

    vm_name = vm['name']
    osdisk = disks['osdisk']
    provisioningdisk = disks.get('provisioningdisk', {})
    ramdisk_path = ramdisk['path']
    ramdisk_size = ramdisk['size']

    # Ensure RAM disk is mounted
    mount_ramdisk(ramdisk_path, ramdisk_size)

    # Destroy and undefine existing VM
    remove_vm(vm_name)

    for disk_key, disk_value in disks.items():
        recreate_disk(disk_value)

    #handle_vm_type("linux")  # Outputs: [INFO] Handling Linux VM
    #handle_vm_type("macos")  # Outputs: [WARN] Unknown VM type: macos
    # Build virt-install command
    virtinstall_cmd = [
        "virt-install",
        "--connect=qemu:///system",
        "--hvm",
        "--cpu", "host",
        "--features", "kvm_hidden=on",
        f"--os-variant={vm['os_variant']}",
        f"--name={vm_name}",
        f"--vcpus={vm['vcpus']}",
        f"--memory={vm['memory']}",
        f"--disk=path={osdisk['uri']},size={osdisk['size']},format={osdisk['format']},bus=virtio,sparse=yes",
    ]

    # Add provisioning disk to virt-install if present
    if provisioningdisk:
        virtinstall_cmd.append(
            f"--disk=path={provisioningdisk['uri']},format={provisioningdisk['format']},bus=virtio"
        )

    # Add network configuration
    network = vmconfig['network']
    if network['type'] == "nat":
        virtinstall_cmd.append(f"--network default,mac={network['mac']},model=virtio")
    elif network['type'] == "bridge":
        virtinstall_cmd.append(f"--network bridge={network['parent_interface']},mac={network['mac']},model=virtio")

    print("[INFO] Running virt-install command:")
    print(" ".join(virtinstall_cmd))
    subprocess_run_wrapper(virtinstall_cmd, check=True)


if __name__ == "__main__":
    main()

