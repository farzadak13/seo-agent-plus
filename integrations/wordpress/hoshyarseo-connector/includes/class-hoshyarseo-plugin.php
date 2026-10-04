<?php
/**
 * Composition root: builds the collaborators once and registers their hooks.
 *
 * @package HoshyarSEO_Connector
 */

defined( 'ABSPATH' ) || exit;

/**
 * Plugin bootstrap.
 */
final class HoshyarSEO_Plugin {

	/**
	 * Single instance.
	 *
	 * @var HoshyarSEO_Plugin|null
	 */
	private static $instance = null;

	/**
	 * Return the single instance.
	 *
	 * @return HoshyarSEO_Plugin
	 */
	public static function instance() {
		if ( null === self::$instance ) {
			self::$instance = new self();
		}
		return self::$instance;
	}

	/**
	 * Register hooks. Called once from the main plugin file.
	 *
	 * @return void
	 */
	public function boot() {
		$store = new HoshyarSEO_Title_Store();
		$cache = new HoshyarSEO_Cache();

		( new HoshyarSEO_Title_Filters( $store ) )->register();

		$controller = new HoshyarSEO_REST_Controller( $store, $cache );
		add_action( 'rest_api_init', array( $controller, 'register_routes' ) );
	}
}
