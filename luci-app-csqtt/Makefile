include $(TOPDIR)/rules.mk

LUCI_TITLE:=LuCI support for CSQTT VPN
LUCI_DEPENDS:=+csqtt +luci-base +ucode +ucode-mod-fs +ucode-mod-uci
LUCI_PKGARCH:=all

PKG_NAME:=luci-app-csqtt
PKG_VERSION:=1.1.0
PKG_RELEASE:=1
PKG_LICENSE:=PolyForm-Noncommercial-1.0.0
PKG_MAINTAINER:=loons1k <loons1k@gmail.com>

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
