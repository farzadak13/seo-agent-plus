<?php
/**
 * Plugin Name:       HoshyarSEO Connector
 * Plugin URI:        https://hoshyarseo.ir/
 * Description:       Lets HoshyarSEO apply page titles you have approved, and undo them exactly. Works alongside Yoast SEO, Rank Math, All in One SEO and SEOPress without changing their settings.
 * Version:           0.1.0
 * Requires at least: 6.0
 * Requires PHP:      7.4
 * Author:            HoshyarSEO
 * License:           GPL-2.0-or-later
 * Text Domain:       hoshyarseo-connector
 *
 * @package HoshyarSEO_Connector
 */

defined( 'ABSPATH' ) || exit;

define( 'HOSHYARSEO_CONNECTOR_VERSION', '0.1.0' );
define( 'HOSHYARSEO_CONNECTOR_DIR', plugin_dir_path( __FILE__ ) );

require_once HOSHYARSEO_CONNECTOR_DIR . 'includes/class-hoshyarseo-title-store.php';
require_once HOSHYARSEO_CONNECTOR_DIR . 'includes/class-hoshyarseo-cache.php';
require_once HOSHYARSEO_CONNECTOR_DIR . 'includes/class-hoshyarseo-title-filters.php';
require_once HOSHYARSEO_CONNECTOR_DIR . 'includes/class-hoshyarseo-rest-controller.php';
require_once HOSHYARSEO_CONNECTOR_DIR . 'includes/class-hoshyarseo-plugin.php';

HoshyarSEO_Plugin::instance()->boot();
