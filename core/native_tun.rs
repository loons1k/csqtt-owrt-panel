// SPDX-FileCopyrightText: 2026 amurcanov
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

//! Native TUN для Linux-роутеров (Keenetic/Entware, OpenWrt).

use anyhow::{Context, Result, bail};
use std::{
    ffi::CString,
    fs::File,
    io,
    os::fd::{FromRawFd, RawFd},
};

const TUN_DEV: &str = "/dev/net/tun";
const IFF_TUN: u16 = 0x0001;
const IFF_NO_PI: u16 = 0x1000;

#[cfg(target_arch = "mips")]
const TUNSETIFF: u64 = 0x800454ca;
#[cfg(not(target_arch = "mips"))]
const TUNSETIFF: u64 = 0x400454ca;

const IFNAMSIZ: usize = 16;

#[repr(C)]
struct Ifreq {
    name: [u8; IFNAMSIZ],
    flags: u16,
    _pad: [u8; 22],
}

impl Default for Ifreq {
    fn default() -> Self {
        Self {
            name: [0u8; IFNAMSIZ],
            flags: 0,
            _pad: [0u8; 22],
        }
    }
}

pub struct CreatedTun {
    pub file: File,
    pub name: String,
}

pub fn create_tun_interface(name: &str, mtu: u32) -> Result<CreatedTun> {
    let cpath = CString::new(TUN_DEV).unwrap();
    let fd = unsafe { libc::open(cpath.as_ptr(), libc::O_RDWR | libc::O_CLOEXEC) };
    if fd < 0 {
        let err = io::Error::last_os_error();
        crate::log_error!(
            "[TUN] open({TUN_DEV}) failed: errno={} ({})",
            err.raw_os_error().unwrap_or(-1),
            err
        );
        return Err(err).context("open /dev/net/tun failed (kernel tun module missing?)");
    }

    let raw_fd = fd;
    let name_bytes = name.as_bytes();
    if !name_bytes.is_empty() && name_bytes.len() >= IFNAMSIZ {
        unsafe { libc::close(fd) };
        bail!(
            "TUN interface name too long (max {} bytes): {name}",
            IFNAMSIZ - 1
        );
    }

    let mut ifr = Ifreq::default();
    ifr.flags = IFF_TUN | IFF_NO_PI;
    if !name_bytes.is_empty() {
        ifr.name[..name_bytes.len()].copy_from_slice(name_bytes);
    }

    let rc = unsafe {
        libc::ioctl(
            raw_fd,
            TUNSETIFF as libc::Ioctl,
            &mut ifr as *mut _ as *mut libc::c_void,
        )
    };
    if rc < 0 {
        let err = io::Error::last_os_error();
        crate::log_error!(
            "[TUN] ioctl(TUNSETIFF) failed: errno={} ({})",
            err.raw_os_error().unwrap_or(-1),
            err
        );
        unsafe { libc::close(fd) };
        return Err(err).with_context(|| format!("ioctl(TUNSETIFF) failed for name='{name}'"));
    }

    let actual_name_end = ifr.name.iter().position(|&b| b == 0).unwrap_or(IFNAMSIZ);
    let actual_name = std::str::from_utf8(&ifr.name[..actual_name_end])
        .unwrap_or("<invalid utf8>")
        .to_owned();
    crate::log_error!("[TUN] Интерфейс создан: {actual_name}, MTU={mtu}");

    let flags = unsafe { libc::fcntl(raw_fd, libc::F_GETFL) };
    if flags >= 0 {
        unsafe { libc::fcntl(raw_fd, libc::F_SETFL, flags | libc::O_NONBLOCK) };
    }

    if mtu > 0 {
        if let Err(e) = set_mtu(&actual_name, mtu) {
            crate::log_error!("[TUN] MTU: {e:#}");
        }
    }
    if let Err(e) = set_up(&actual_name) {
        crate::log_error!("[TUN] UP: {e:#}");
    }

    let file = unsafe { File::from_raw_fd(raw_fd) };
    Ok(CreatedTun {
        file,
        name: actual_name,
    })
}

pub fn set_interface_address(iface: &str, ip_str: &str) -> Result<()> {
    crate::log_error!("[TUN] Конфигурация: {iface} = {ip_str}/32");
    configure_address(iface, ip_str).with_context(|| format!("set IP {ip_str} on {iface}"))?;

    if let Ok(ip) = ip_str.parse::<std::net::Ipv4Addr>() {
        let octets = ip.octets();
        let subnet = format!("{}.{}.{}.0/24", octets[0], octets[1], octets[2]);
        crate::log_error!("[TUN] Авто-маршрут подсети: {subnet} dev {iface}");
        let _ = std::process::Command::new("ip")
            .args(["route", "replace", &subnet, "dev", iface])
            .status();
    }
    Ok(())
}

fn configure_address(iface: &str, ip_str: &str) -> Result<()> {
    use std::net::Ipv4Addr;
    use std::str::FromStr;
    let ip = Ipv4Addr::from_str(ip_str).with_context(|| format!("invalid IPv4: {ip_str}"))?;
    let sock = unsafe { libc::socket(libc::AF_INET, libc::SOCK_DGRAM, 0) };
    if sock < 0 {
        return Err(io::Error::last_os_error()).context("socket failed");
    }
    let _guard = FileFdGuard(sock);

    #[repr(C)]
    struct IfreqAddr {
        name: [u8; IFNAMSIZ],
        addr: SockaddrIn,
        _pad: [u8; 8],
    }
    #[repr(C)]
    struct SockaddrIn {
        family: u16,
        port: u16,
        addr: [u8; 4],
        zero: [u8; 8],
    }
    let mut ifr = IfreqAddr {
        name: [0u8; IFNAMSIZ],
        addr: SockaddrIn {
            family: libc::AF_INET as u16,
            port: 0,
            addr: ip.octets(),
            zero: [0u8; 8],
        },
        _pad: [0u8; 8],
    };
    let nb = iface.as_bytes();
    if nb.len() >= IFNAMSIZ {
        bail!("name too long");
    }
    ifr.name[..nb.len()].copy_from_slice(nb);

    const SIOCSIFADDR: libc::Ioctl = 0x8916;
    const SIOCSIFDSTADDR: libc::Ioctl = 0x8918;
    const SIOCSIFNETMASK: libc::Ioctl = 0x891c;

    let rc = unsafe { libc::ioctl(sock, SIOCSIFADDR, &mut ifr) };
    if rc < 0 {
        return Err(io::Error::last_os_error()).context("SIOCSIFADDR failed");
    }

    // Устанавливаем peer/gateway для Point-to-Point интерфейса (x.x.x.1)
    let peer_ip = [ip.octets()[0], ip.octets()[1], ip.octets()[2], 1];
    ifr.addr.addr = peer_ip;
    let _ = unsafe { libc::ioctl(sock, SIOCSIFDSTADDR, &mut ifr) };

    ifr.addr.addr = [255, 255, 255, 255];
    let rc = unsafe { libc::ioctl(sock, SIOCSIFNETMASK, &mut ifr) };
    if rc < 0 {
        return Err(io::Error::last_os_error()).context("SIOCSIFNETMASK failed");
    }
    Ok(())
}

fn set_mtu(iface: &str, mtu: u32) -> Result<()> {
    let sock = unsafe { libc::socket(libc::AF_INET, libc::SOCK_DGRAM, 0) };
    if sock < 0 {
        return Err(io::Error::last_os_error()).context("socket failed");
    }
    let _guard = FileFdGuard(sock);
    #[repr(C)]
    struct IfreqMtu {
        name: [u8; IFNAMSIZ],
        mtu: i32,
        _pad: [u8; 20],
    }
    let mut ifr = IfreqMtu {
        name: [0u8; IFNAMSIZ],
        mtu: mtu as i32,
        _pad: [0u8; 20],
    };
    let nb = iface.as_bytes();
    if nb.len() >= IFNAMSIZ {
        bail!("name too long");
    }
    ifr.name[..nb.len()].copy_from_slice(nb);
    const SIOCSIFMTU: libc::Ioctl = 0x8922;
    let rc = unsafe { libc::ioctl(sock, SIOCSIFMTU, &mut ifr) };
    if rc < 0 {
        return Err(io::Error::last_os_error()).context("SIOCSIFMTU failed");
    }
    Ok(())
}

fn set_up(iface: &str) -> Result<()> {
    let sock = unsafe { libc::socket(libc::AF_INET, libc::SOCK_DGRAM, 0) };
    if sock < 0 {
        return Err(io::Error::last_os_error()).context("socket failed");
    }
    let _guard = FileFdGuard(sock);
    #[repr(C)]
    struct IfreqFlags {
        name: [u8; IFNAMSIZ],
        flags: u16,
        _pad: [u8; 22],
    }
    const IFF_UP: u16 = 0x1;
    const IFF_RUNNING: u16 = 0x40;
    const SIOCGIFFLAGS: libc::Ioctl = 0x8913;
    const SIOCSIFFLAGS: libc::Ioctl = 0x8914;
    let mut ifr = IfreqFlags {
        name: [0u8; IFNAMSIZ],
        flags: 0,
        _pad: [0u8; 22],
    };
    let nb = iface.as_bytes();
    if nb.len() >= IFNAMSIZ {
        bail!("name too long");
    }
    ifr.name[..nb.len()].copy_from_slice(nb);
    let rc = unsafe { libc::ioctl(sock, SIOCGIFFLAGS, &mut ifr) };
    if rc < 0 {
        return Err(io::Error::last_os_error()).context("SIOCGIFFLAGS failed");
    }
    ifr.flags |= IFF_UP | IFF_RUNNING;
    let rc = unsafe { libc::ioctl(sock, SIOCSIFFLAGS, &ifr) };
    if rc < 0 {
        return Err(io::Error::last_os_error()).context("SIOCSIFFLAGS failed");
    }
    Ok(())
}

struct FileFdGuard(RawFd);
impl Drop for FileFdGuard {
    fn drop(&mut self) {
        unsafe { libc::close(self.0) };
    }
}
