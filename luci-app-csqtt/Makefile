include $(TOPDIR)/rules.mk

LUCI_TITLE:=LuCI support for CSQTT (VK/OK Video Call UDP Tunnel)
LUCI_DEPENDS:=+luci-base +kmod-tun +curl +ca-bundle +ucode +ucode-mod-fs +ucode-mod-uci
LUCI_PKGARCH:=all

PKG_NAME:=luci-app-csqtt
PKG_VERSION:=2.5.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
